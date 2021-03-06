# Original imports, will try to cull these a bit.
import os
import sys
import datetime
import numpy as np
import shutil
import subprocess
import tarfile
from collections import namedtuple

from osgeo import gdal

from snowmelt.utils import mkdir_p
from snowmelt import config

# TODO refactor structure to avoid these kinds of imports
sys.path.append('{}/software/grid2dss'.format(os.getenv('CWMSGRID_HOME')))
from hecgridloaders import flt2dss

# Global vars.  TODO Bit ugly, need to rethink how to do these.
Extent = namedtuple('Extent', 'xmin,ymin,xmax,ymax')  # Convert to a class?

SNODAS_FILENAME_LIST = [
    "{ds}_ssmv11034tS__T0001TTNATS{ymd}05HP001",
    "{ds}_ssmv11036tS__T0001TTNATS{ymd}05HP001",
    "{ds}_ssmv11038wS__A0024TTNATS{ymd}05DP001",
    "{ds}_ssmv11044bS__T0024TTNATS{ymd}05DP000",
]


def print_dashes(length=64):
    print('-' * length)


def prepare_source_data_for_date(process_date, src_dir, save_tiff=True):
    ''' Builds an unzip directory and extracts data from source files
    for a given day.
    Returns the directory path to the unzipped files,
    or None if missing any source data. '''
    ymd_str = process_date.strftime('%Y%m%d')
    unzip_dir = os.path.join(config.PROCESSED_SRC_DIR, 'unzipped_data', ymd_str)
    us_tif_dir = os.path.join(config.PROCESSED_SRC_DIR, 'conus_tiffs')

    # Use 'us' prefix and adjust nodata value for dates before January 24, 2011.
    ds_type = 'zz'
    nodata_val = '-9999'
    if process_date < datetime.datetime(2011, 1, 24, 0, 0):
        ds_type = 'us'
        nodata_val = '55537'

    masterhdr = os.path.join(config.HEADER_KEY_DIR, ds_type + '_master.hdr')

    # Create list of file names for this date.
    snodas_src_files = [
        f.format(ds=ds_type, ymd=ymd_str) for f in SNODAS_FILENAME_LIST
    ]

    # Make sure all files exist before trying any extractions.
    print_dashes()
    print('Processing source data for: {}'.format(process_date.strftime('%Y.%m.%d')))
    msgs = []
    for filename in snodas_src_files:
        _file = os.path.join(src_dir, filename + '.grz')
        if not os.path.isfile(_file):
            msgs += ['Missing source data file: {0}'.format(_file)]
    if msgs:
        for msg in msgs:
            print(msg)
        print_dashes()
        return None

    if save_tiff:
        mkdir_p(us_tif_dir)

    # Loop through our filenames and do the unzipping and other set up.
    mkdir_p(unzip_dir)
    for filename in snodas_src_files:
        src_file = os.path.join(src_dir, filename)
        unzip_file = os.path.join(unzip_dir, filename)
        ready_file = unzip_file + '.bil'
        if not os.path.isfile(ready_file):
            print('Processing source to output file: {}'.format(ready_file))
            try:
                UnzipLinux(src_file, unzip_file)
                RawFileManip(unzip_file, masterhdr)
            except:
                print('ERROR: Failure in UnzipLinux or RawFileManip')
                return None
        else:
            print('Using existing source file: {}'.format(ready_file))

        # Save a full version of the day's data set.
        shgtif = os.path.join(us_tif_dir, filename + 'alb.tif')
        if save_tiff:
            if not os.path.isfile(shgtif):
                print('Saving CONUS SHG tiff file: {}'.format(shgtif))
                ReprojUseWarpBil(ready_file, shgtif, nodata=nodata_val,
                                 tr_x='1000', tr_y='-1000')
            else:
                print('CONUS SHG tiff already exists: {}'.format(shgtif))

    print_dashes()
    return unzip_dir


def process_extents(office_symbol, process_date,
                    src_dir, extents_list, options):
    ''' Main function for processing extents.  Calls lots of helper
    and utility functions.
    office_symbol: string - unique office symbol, used in output file format
    process_date: datetime.datetime object - date for which data is desired.
    extents_list: list - list of namedtuples for each watershed.

    Returns the path to the file if new data was written to a DSS file,
    None otherwise.
    '''

    def verbose_print(to_print):
        if options.verbose:
            print(to_print)

    def clean_up_tmp_dir(tmp_dir):
        if not options.keep_tmp_dir:
            shutil.rmtree(tmp_dir)

    verbose_print('Source directory: {0}'.format(src_dir))

    # Use 'us' prefix and adjust nodata value for dates before January 24, 2011.
    dataset_type = 'zz'
    nodata_val = '-9999'
    if process_date < datetime.datetime(2011, 1, 24, 0, 0):
        dataset_type = 'us'
        nodata_val = '55537'

    # Use the proper results dir structure based on the config file.
    projfltdir = os.path.join(config.FLT_BASE_DIR, office_symbol)
    projdssdir = os.path.join(config.DSS_BASE_DIR, office_symbol)
    histdir = os.path.join(config.HISTORY_BASE_DIR, office_symbol)
    tmpdir = os.path.join(projfltdir, 'tmp{}'.format(datetime.datetime.now().strftime('%y%m%d%H%M%S')))

    # Build our results directories if needed.
    for d in (projfltdir, projdssdir, histdir, tmpdir):
        mkdir_p(d)

    # Break out if processing for the given date has already happened.
    histfile = os.path.join(histdir, 'proccomplete{}.txt'.format(process_date.strftime('%Y%m%d')))
    if os.path.isfile(histfile):
        print('{0} grids already processed for: {1}'.format(office_symbol, process_date.strftime('%Y.%m.%d')))
        return None

    print('Processing {0} grids for: {1}'.format(office_symbol, process_date.strftime('%Y.%m.%d')))

    # Set up a dictionary mapping the various properties to their DSS names.
    PropDict = SetProps(process_date, office_symbol)
    scratchfile_dict = {}
    extentGProps = {}
    maxExtent = getMaxExtent(extents_list)
    dssfile = os.path.join(projdssdir, '{}'.format(GetDSSBaseName(process_date)))

    # Instantiate new flt2dss.Operation
    flt2dss_operation = flt2dss.Operation()

    # Loop through our source SNODAS files.
    for f in [s.format(ds=dataset_type, ymd=process_date.strftime('%Y%m%d')) for s in SNODAS_FILENAME_LIST]:

        # Strip variable key/ID from filename
        varcode = f[8:12]
        varprops = PropDict[varcode]

        # Filenames
        src_file = os.path.join(src_dir, '{}.bil'.format(f))
        shgtif = os.path.join(tmpdir, '{}alb.tif'.format(f))
        shgtifmath = os.path.join(tmpdir, '{}.tif'.format(f))

        # Reproject src_file to shgtif
        ReprojUseWarpBil(src_file, shgtif, maxExtent, nodata_val)

        # Set dictionary entry for variable to filepath to SHG projected TIF
        scratchfile_dict[varcode] = shgtif
        
        # if variable does not require computation to define grid values, set
        # "shgtifmath" (grid after computation) to shgtif (raw grid)
        # else run RasterMath() to write a grid with computed values
        mathranokay = True
        # If math is not required to derive final grid
        if varprops[2] is False:
            shgtifmath = shgtif
        else:
            # NOTE: scratchfile_dict populated only for prior product numbers
            mathranokay = RasterMath(shgtif, shgtifmath, varcode, scratchfile_dict)

        if mathranokay:
            scratchfile_dict[varcode] = shgtifmath
            for extentarr in extents_list:
                ds = gdal.Open(shgtifmath)
                if ds is None:
                    print('Could not open {}'.format(shgtifmath))
                    return None
                nodata = ds.GetRasterBand(1).GetNoDataValue()
                fullext = GetDatasetExtent(ds)
                cellsize = ds.GetGeoTransform()[1]

                extent_name, subext = extentarr[0], extentarr[1]
                fullof, subof = min_box_os(fullext, subext, cellsize)
                xsize = int(fullof[2])
                ysize = int(fullof[3])
                dsProj = ds.GetProjection()

                cliparr = ds.GetRasterBand(1).ReadAsArray(
                    int(round(fullof[0])), int(round(fullof[1])),
                    xsize, ysize
                )

                clipgeot = [subext[0], cellsize, 0, subext[3], 0, -cellsize]
                extentGProps[extent_name] = [dsProj, clipgeot, xsize, ysize, nodata]

                driver = gdal.GetDriverByName("MEM")
                clipds = driver.Create("", xsize, ysize, 1, gdal.GDT_Float32)
                clipds.SetGeoTransform(clipgeot)
                clipds.SetProjection(ds.GetProjection())
                clipds.GetRasterBand(1).SetNoDataValue(nodata)
                clipds.GetRasterBand(1).WriteArray(cliparr, 0, 0)
                clipds.FlushCache()
                
                file_basename1 = '{}_{}{}'.format(extent_name.replace(" ", "_"),
                                                  varprops[0][2].replace(" ", "_").lower(),
                                                  process_date.strftime('%Y.%m.%d')
                                                  )

                # Write grid to "file_basename1" in the tmpdir
                WriteGrid(clipds, file_basename1, tmpdir, config.SCRATCH_FILE_DRIVER)
                cliparr = None
                clipds = None
                ds = None

                # Create a flt2dss Task
                flt2dss_task = flt2dss.Task(
                    infile=os.path.join(tmpdir, '{}.{}'.format(file_basename1, 'bil')),
                    dss_file=dssfile,
                    data_type=varprops[1],
                    pathname='/SHG/{}/{}/{}/{}/{}/'.format(extentarr[0].upper(), varprops[0][2], varprops[0][3], varprops[0][4], varprops[0][5]),
                    grid_type='SHG',
                    data_unit=varprops[3]
                    )

                # Add flt2dss Task to Operation
                flt2dss_operation.add_task(flt2dss_task)

    if len(extentGProps) == 0:
        print("An error occurred identifying extent properties.")
        clean_up_tmp_dir(tmpdir)
        return None

    # Write Zero Grids for LIQUID WATER, COLD CONTENT ATI, MELTRATE ATI
    for varcode in ["0001", "0002", "0003"]:
        varprops = PropDict[varcode]       
        for extentarr in extents_list:

            file_basename2 = '{}_{}{}'.format(extentarr[0].replace(" ", "_"),
                                              varprops[0][2].replace(" ", "_").lower(),
                                              process_date.strftime('%Y%m%d')
                                              )

            # Write grid to "file_basename1" in the tmpdir
            WriteZeroGrid(extentGProps[extentarr[0]], file_basename2, tmpdir, config.SCRATCH_FILE_DRIVER)

            # Create a flt2dss Task
            flt2dss_task = flt2dss.Task(
                infile=os.path.join(tmpdir, '{}.{}'.format(file_basename2, 'bil')),
                dss_file=dssfile,
                data_type=varprops[1],
                pathname='/SHG/{}/{}/{}/{}/{}/'.format(extentarr[0].upper(), varprops[0][2], varprops[0][3], varprops[0][4], varprops[0][5]),
                grid_type='SHG',
                data_unit=varprops[3]
                )

            # Add flt2dss Task to Operation
            flt2dss_operation.add_task(flt2dss_task)
    
    # Write grids to DSS
    flt2dss_operation.execute()

    clean_up_tmp_dir(tmpdir)

    # Write out file to track that we've run for this day.
    with open(histfile, "w") as fo:
        fo.write(process_date.strftime("%a %b %d %H:%M:%S %Y"))
        fo.close
    return dssfile


########################################################################
# Helper functions below.
########################################################################

def WriteGrid(inds, gridname, tmpdir, driver_name):
    if driver_name == 'AAIGrid':
        fileext = 'asc'
    elif driver_name == 'EHdr':
        fileext = 'bil'
    else:
        print('dss gridloader unsupported for driver_name: {}'.format(driver_name))
        sys.exit()

    outgrid = os.path.join(tmpdir, gridname + '.' + fileext)
    driver = gdal.GetDriverByName(driver_name)
    outds = driver.CreateCopy(outgrid, inds, 0, options=[])
    outds = None
    return


def GetDatasetExtent(ds):
    """Usage: get_extent(input_dataset)  """
    geot = ds.GetGeoTransform()
    cellsize = geot[1]
    return ([geot[0], geot[3] - (ds.RasterYSize * cellsize),
             geot[0] + (ds.RasterXSize * cellsize), geot[3]])


def GetDSSBaseName(inDT):
    # snow.<yyyy>.<mm>.dss
    # Account for dss using 2400 as midnight And nws data using 0000
    if inDT.strftime("%H") == "00" and inDT.strftime("%d") == "01":
        return "snow." + (inDT - datetime.timedelta(hours=1)).strftime("%Y.%m.dss")
    else:
        return "snow." + inDT.strftime("%Y.%m.dss")


def GetGridExtent(infile):
    driver = gdal.GetDriverByName("AIG")
    ds = gdal.Open(infile, gdal.GA_ReadOnly)
    if ds is None:
        raise IOError("Could not open '%s'" % (infile))

    geot = ds.GetGeoTransform()
    cellsize = geot[1]
    YSize = ds.RasterYSize
    XSize = ds.RasterXSize
    ds = None
    return (Extent(geot[0], geot[3] - (YSize * cellsize),
                   geot[0] + (XSize * cellsize), geot[3]))


def getMaxExtent(extents):
    xmin = extents[0][1][0]
    ymin = extents[0][1][1]
    xmax = extents[0][1][2]
    ymax = extents[0][1][3]
    for ext in extents:
        ecoords = ext[1]
        xmin = min(xmin, ecoords[0])
        ymin = min(ymin, ecoords[1])
        xmax = max(xmax, ecoords[2])
        ymax = max(ymax, ecoords[3])
    return Extent(xmin, ymin, xmax, ymax)


def is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


def min_box_os(ext1, ext2, cellsize):
    """
    Calculate minimum bounding box for two input extents and matching
    cellsize.
    Returns 2 lists containing xoffset, yoffset, xsize, ysize for each input
    extent.  These can be used to subset images using ReadAsArray (e.g.)
    Input extents needs to be lists of (xmin,ymin,xmax,ymax).
    """
    maxxl = max(ext1[0], ext2[0])
    minxr = min(ext1[2], ext2[2])
    maxyb = max(ext1[1], ext2[1])
    minyt = min(ext1[3], ext2[3])

    offx1 = 0.0
    offx2 = 0.0
    offy1 = 0.0
    offy2 = 0.0

    if ext1[0] < maxxl:
        offx1 = (maxxl - ext1[0]) / cellsize
    else:
        offx2 = (maxxl - ext2[0]) / cellsize
    if ext1[3] > minyt:
        offy1 = (ext1[3] - minyt) / cellsize
    else:
        offy2 = (ext2[3] - minyt) / cellsize

    xsize = (minxr - maxxl) / cellsize
    ysize = (minyt - maxyb) / cellsize

    os1 = (offx1, offy1, xsize, ysize)
    os2 = (offx2, offy2, xsize, ysize)

    return os1, os2


def RawFileManip(file_noext, masterhdr):
    ''' Replaces header with custom header file and renames .dat to .bil '''
    os.remove(file_noext + ".Hdr")
    shutil.copy(masterhdr, file_noext + ".hdr")
    if os.path.exists(file_noext + ".bil"):
        os.remove(file_noext + ".bil")
    os.rename(file_noext + ".dat", file_noext + ".bil")
    return file_noext + ".bil"


def ReprojUseWarpBil(infile, outfile, ext=None, nodata='-9999',
                     tr_x='2000', tr_y='-2000'):
    # From Spatial Reference WGS 84 to NAD83 Conus Albers
    to_srs = '"EPSG:5070"'
    from_srs = '"EPSG:4326"'

    cmdlist = ["gdalwarp", "-s_srs", from_srs, "-t_srs", to_srs,
                "-r", "bilinear",
                "-srcnodata", nodata,
                "-dstnodata", nodata,
                "-tr", tr_x, tr_y, "-tap"]
    if ext is not None:
        cmdlist += ["-te", str(ext.xmin), str(ext.ymin),
                           str(ext.xmax), str(ext.ymax),]
    cmdlist += [infile, outfile]
                        
    run_cmd = ' '.join(cmdlist)
    if not config.SUBPROCESS_QUIET:
        print(run_cmd)
    proc = subprocess.Popen(run_cmd, shell=True,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    stdout, stderr = proc.communicate()
    exit_code = proc.wait()

    if not config.SUBPROCESS_QUIET:
        print(stdout)
    if exit_code:
        raise RuntimeError(stderr)
    return outfile


def RasterMath(shgtif, shgtifmath, varcode, nameDict):
    # VarCode 1038:  Converts snow pack temp to CC.  ** Assumes that
    #   1034 data (swe) listed in nameDict is same size as 1038 data.  This
    #   is the case currently because of gdalwarp process.

    driver = gdal.GetDriverByName("GTiff")
    ds = gdal.Open(shgtif, gdal.GA_ReadOnly)
    if ds is None:
        return False

    in_geot = ds.GetGeoTransform()

    xsize = ds.RasterXSize
    ysize = ds.RasterYSize

    band = ds.GetRasterBand(1)
    nodata = band.GetNoDataValue()
    arr = band.ReadAsArray(0, 0, xsize, ysize).astype(np.dtype("float32"))

    if varcode == "1038":
        # Cold Content
        sweasc = nameDict["1034"]

        # Make sure the SWE dataset has already been written.
        sweds = gdal.Open(sweasc, gdal.GA_ReadOnly)
        if sweds is None:
            return False

        # SWE ds will have same boundaries and cell size as cold content.
        swearr = sweds.GetRasterBand(1).ReadAsArray(
            0, 0, xsize, ysize).astype(np.dtype("float32"))
        ccarr = np.where(arr == nodata, nodata, arr - 273.15)
        newarr = np.where(ccarr >= 0, 0,
                          np.where((swearr == nodata) | (ccarr == nodata),
                                   nodata, swearr * 2114 * ccarr / 333000))
    elif varcode == "1044":
        # Snow Melt
        newarr = np.where(arr == nodata, nodata, arr / 100.0)
    else:
        newarr = arr

    dsout = driver.Create(shgtifmath, xsize, ysize, 1, gdal.GDT_Float32,
                          options=['COMPRESS=LZW'])
    dsout.SetGeoTransform(in_geot)
    dsout.SetProjection(ds.GetProjection())
    dsout.GetRasterBand(1).SetNoDataValue(nodata)
    dsout.GetRasterBand(1).WriteArray(newarr)
    dsout.FlushCache()
    dsout.GetRasterBand(1).GetStatistics(0, 1)

    # Close any potentially open datasets.
    dsout = None
    newarr = None
    swearr = None
    ccarr = None
    sweds = None
    arr = None
    band = None
    ds = None

    return True


def SetProps(inDate, basin):
    # dict[0] = Pathname Part list
    # dict[1] = Data type
    # dict[2] = Run var thru RasterMath sub
    # dict[3] = Data Units

    DSSdate = '{}:0600'.format(inDate.strftime("%d%b%Y").upper())
    DSSdateYest = '{}:0600'.format((inDate - datetime.timedelta(1)).strftime("%d%b%Y").upper())
    bup = basin.upper()

    return { '1034': [["SHG", bup, "SWE", DSSdate, "", "SNODAS"], "INST-VAL", False, 'MM'],
             '1036': [["SHG", bup, "SNOW DEPTH", DSSdate, "", "SNODAS"], "INST-VAL", False, 'MM'],
             '1038': [["SHG", bup, "COLD CONTENT", DSSdate, "", "SNODAS"], "INST-VAL", True, 'MM'],
             '1044': [["SHG", bup, "SNOW MELT", DSSdateYest, DSSdate, "SNODAS"], "PER-CUM", True, 'MM'],
             '0001': [["SHG", bup, "LIQUID WATER", DSSdate, "", "ZERO"], "INST-VAL", False, "MM"],
             '0002': [["SHG", bup, "COLD CONTENT ATI", DSSdate, "", "ZERO"], "INST-VAL", False, "DEG C"],
             '0003': [["SHG", bup, "MELTRATE ATI", DSSdate, "", "ZERO"], "INST-VAL", False, "DEGC-D"]
             }

def UnzipLinux(origfile_noext, file_noext):
    ''' Extract our tarball of data. '''
    if not os.path.exists(origfile_noext + '.grz'):
        print('File does not exist: ' + file_noext + '.grz')
        sys.exit()

    bname = os.path.basename(file_noext)
    pname = os.path.dirname(file_noext)

    OUTPUT_EXTS = ('.Hdr', '.dat')
    for output_ext in OUTPUT_EXTS:
        if os.path.exists(file_noext + output_ext):
            os.remove(file_noext + output_ext)

    tar = tarfile.open(origfile_noext + '.grz', 'r')
    tar.extractall(pname)

    # Do one more layer of extraction if needed.
    for output_ext in OUTPUT_EXTS:
        gz_filename = file_noext + output_ext + '.gz'
        if os.path.isfile(file_noext + output_ext + '.gz'):
            cmdlist = ['gunzip', gz_filename]
            proc = subprocess.Popen(cmdlist, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            stdout, stderr = proc.communicate()
            exit_code = proc.wait()


def WriteZeroGrid(gProps, gridname, tmpdir, driver_name):
    xsize = gProps[2]
    ysize = gProps[3]

    memdrv = gdal.GetDriverByName("MEM")
    memds = memdrv.Create("", xsize, ysize, 1, gdal.GDT_Byte)
    memds.SetProjection(gProps[0])
    memds.SetGeoTransform(gProps[1])
    memds.GetRasterBand(1).SetNoDataValue(-9999)
    ndarr = np.zeros([ysize, xsize], np.dtype('byte'))
    memds.GetRasterBand(1).WriteArray(ndarr, 0, 0)
    memds.FlushCache()

    WriteGrid(memds, gridname, tmpdir, driver_name)

    ndarr = None
    memds = None
