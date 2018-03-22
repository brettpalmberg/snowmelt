#! ame usr/bin/bash

# Variables for us data stream
#swe=us_ssmv11034tS__T0001TTNATS
#snowdepth=us_ssmv11036tS__T0001TTNATS
#snowavgtemp=us_ssmv11038wS__A0024TTNATS
#snowmelt=us_ssmv11044bS__T0024TTNATS

# Example tar link
# ftp://sidads.colorado.edu/DATASETS/NOAA/G02158/unmasked/2013/03_Mar/SNODAS_unmasked_20130326.tar

# Variables for zz data stream
swe2=zz_ssmv11034tS__T0001TTNATS
snowdepth2=zz_ssmv11036tS__T0001TTNATS
snowavgtemp2=zz_ssmv11038wS__A0024TTNATS
snowmelt2=zz_ssmv11044bS__T0024TTNATS

# Command line args
dth=$1
target=$2

# Move to our intermediate directory and clear it out.
cd /fire/study/snow/nohrsc_gdal/backfill_snodas
rm *.tar *.gz

# Grab the file.
/usr/bin/wget -N $target

# Untar it.
tar -xvf SNODAS_unmasked_"$dth".tar

# Package up the 4 parts we care about.
tar -cvzf data_prep/"$swe2""$dth"05HP001.grz "$swe2""$dth"05HP001*.gz 
tar -cvzf data_prep/"$snowdepth2""$dth"05HP001.grz "$snowdepth2""$dth"05HP001*.gz 
tar -cvzf data_prep/"$snowavgtemp2""$dth"05DP001.grz "$snowavgtemp2""$dth"05DP001*.gz 
tar -cvzf data_prep/"$snowmelt2""$dth"05DP000.grz "$snowmelt2""$dth"05DP000*.gz 

# Move the created grz files to the target raw dir file
mv data_prep/*.grz /fire/study/snow/rawdata

# Clear out the backfill folder of the intermediate data.
cd /fire/study/snow/nohrsc_gdal/backfill_snodas
rm *.tar *.gz