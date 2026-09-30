#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#Copyright (c) 2020-2024 - University of Liège - Digital Energy and Agriculture Lab (DEAL)
#Authors : Roxane Bruhwyler (roxane.bruhwyler@uliege.be or roxane.bruhwyler@hotmail.com) and Nicolas De Cock (nicolas.decock1@gmail.com)
#This file is part of the PASE software, and is distributed under the MIT license.
from matplotlib.patches import Polygon
from scipy.spatial import cKDTree
from calendar import isleap
import logging
import numpy as np
import pandas as pd
import pyvista as pyV
import pvlib.solarposition as pvlibSP
import sys
from pvlib.atmosphere import get_relative_airmass
from pvlib.irradiance import get_extra_radiation
import matplotlib.pyplot as plt
import os

import pase.conversion_functions as cf
from pase.DATA_MANAGEMENT.visualization_in_3D import open_pyvista_3D_visualization
from pase.ENVIRONMENT.sky_model import ReinhartSky, fibonacci_half_sphere
from pase.ENVIRONMENT.sky_model import CIEStandardSky
from pase.paths import static_data_path
from pase.user_support_tools import PASE_Logger
from pase.ENVIRONMENT.shading import Horizon

logger = logging.getLogger(__name__)


def trace_rays(geometry, origins, directions, first_point=False, retry=False):
    """Trace rays with Embree when enabled, or a cached VTK locator for Studio."""
    origins = np.asarray(origins, dtype=float)
    directions = np.asarray(directions, dtype=float)
    if os.environ.get("PASE_SAFE_RAYCAST") != "1" and sys.platform != "win32":
        return geometry.multi_ray_trace(origins, directions, first_point=first_point, retry=retry)

    hit_points, hit_rays, hit_cells = [], [], []
    report_step = max(1, len(origins) // 20)
    for ray_id, (origin, direction) in enumerate(zip(origins, directions)):
        if os.environ.get("PASE_STUDIO") == "1" and ray_id % report_step == 0:
            print(f"PASE Studio: traçando raios {ray_id}/{len(origins)}", flush=True)
        direction_length = np.linalg.norm(direction)
        if direction_length <= 1e-12:
            continue
        endpoint = origin + direction * (10000.0 / direction_length)
        points, cells = geometry.ray_trace(origin, endpoint, first_point=False)
        if not len(cells):
            continue
        distances = np.linalg.norm(points - origin, axis=1)
        order = np.argsort(distances)
        if first_point:
            order = order[:1]
        hit_points.extend(points[order])
        hit_cells.extend(cells[order])
        hit_rays.extend([ray_id] * len(order))

    return (np.asarray(hit_points, dtype=float).reshape((-1, 3)),
            np.asarray(hit_rays, dtype=np.int64),
            np.asarray(hit_cells, dtype=np.int64))

def get_sun_vector(beta, gamma):
    #Vectorial based system = {0,East=X, North=Y, Zenith=Z}
    #beta : sun elevation (from -90 to 90°), negative angle means it's night
    #gamma : azimuth from north to east
    gamma, beta = gamma*np.pi/180, beta*np.pi/180
    solar_vector = np.zeros((len(gamma),3))
    solar_vector[:,0]=np.sin(gamma)*np.cos(beta)
    solar_vector[:,1]=np.cos(gamma)*np.cos(beta)
    solar_vector[:,2]=np.sin(beta)
    #SOURCE : Kevin Anderson and Mark Mikofski, Slope-Aware Backtracking for Single-Axis Trackers, NREL
    return solar_vector


class Sun_positions:
    
    def __init__(self, lat, long, freq_deter, TZ):
        
        self.lat = lat
        self.long = long
        self.get_solar_positions(freq_deter, TZ)
        
    def get_solar_positions(self, freq_deter, TZ):
        
        if (freq_deter == 8760 or freq_deter == 8784):
            frq = '1h'
            n = 1
        elif (freq_deter == 35040 or freq_deter == 35136):
            frq = '15min'
            n = 4
        elif (freq_deter == 52560 or freq_deter == 52704):
            frq = '10min'
            n = 6
            
        self.SD_leap_year(frq, n, TZ)
        self.SD_nonleap_year(frq, n, TZ)
            
    def SD_leap_year(self, frq, n, TZ):
            
        index_leap_year = pd.date_range(start='2008-01-01 00:00', freq=frq, 
                                       periods=366*24*n, tz=TZ)        
        self.sp_leapY = pvlibSP.get_solarposition(index_leap_year, 
                                                  self.lat, 
                                                  self.long)
        
        self.sun_vect_leapY = get_sun_vector(self.sp_leapY['elevation'],
                                                  self.sp_leapY['azimuth'])
        
        self.sp_leapY['Top_atm_radiation'] = self.get_top_of_atm_radiation(
            index_leap_year)
        
    def SD_nonleap_year(self, frq, n, TZ):
        
        index_com_year = pd.date_range(start='2005-01-01 00:00', freq=frq, 
                                       periods=365*24*n, tz=TZ)       
        self.sp_nonleapY = pvlibSP.get_solarposition(index_com_year, 
                                                     self.lat, 
                                                     self.long)
        
        self.sun_vect_nonleapY = get_sun_vector(self.sp_nonleapY['elevation'],
                                                     self.sp_nonleapY['azimuth'])
        
        self.sp_nonleapY['Top_atm_radiation'] = self.get_top_of_atm_radiation(
            index_com_year)
        
    def get_top_of_atm_radiation(self, index):
        """
        Compute extraterrestrial (i.e. at the top of the atmosphere) radiation on a
        horizontal surface.

        Source: John A. Duffie, William A. Beckman(auth.)- Solar Engineering of Thermal Processes,
        Fourth Edition (2013), page 37, equation 1.10.2

        :param index: timestamp in %Y-%m-%d %H:%M:%S format
        :type index: DatetimeIndex day of the year (numeric)
        :return: irradiance at top of atmosphere on a horizontal surface [W/m²]
        """
        day_of_year = np.array(index.dayofyear)
        n_days_in_year = day_of_year[len(index)-1]
        solar_declination = self.get_solar_declination(day_of_year)  # [deg]
        solar_hour_angle = self.get_solar_hour_angle(day_of_year, index)  # [rad]
        
        lat_rad = self.lat*np.pi/180  # [rad]
        solar_cst = 1367  # [W/m²]
        top_of_atm_radiation = solar_cst*(np.ones(len(index)) +
                                0.033*np.cos((360*day_of_year/n_days_in_year)*np.pi/180))* \
                                (np.cos(lat_rad)*np.cos(solar_declination)*
                                np.cos(solar_hour_angle*np.pi/180) +
                                np.sin(lat_rad)*np.sin(solar_declination))  # [W/m²]

        # Clip negative values to 0
        top_of_atm_radiation[top_of_atm_radiation<0] = 0

        return top_of_atm_radiation
        
    def get_solar_declination(self, day_of_year):
        """
        Compute the declination, i.e. angular position of the sun at solar noon
        relative to the plane of the equator, approximately between +/-23.45 (degrees).
        Uses pvlib.solarposition.declination_spencer71
        (source: https://pvlib-python.readthedocs.io/en/v0.9.0/generated/pvlib.solarposition.declination_spencer71.html)

        :param day_of_year: day of the year (numeric)
        :type day_of_year: int
        :return: declination angle in radians
        :rtype: ndarray
        """
        declination = pvlibSP.declination_spencer71(day_of_year)
        
        return declination
    
    def get_solar_hour_angle(self, day_of_year, index):
        """
        Compute the hour angle in local solar time, i.e. the angular displacement of the
        sun east or west of the local meridian  due to rotation of the earth on its axis
        at 15◦ per hour; morning negative, afternoon positive. Zero at local solar noon.

        Source: https://pvlib-python.readthedocs.io/en/stable/reference/generated/pvlib.solarposition.hour_angle.html

        :param day_of_year: day of the year (numeric)
        :type day_of_year: int
        :param index: timestamp in %Y-%m-%d %H:%M:%S format
        :type index: DatetimeIndex day of the year (numeric)
        :return: solar_hour_angle in degrees
        :rtype: ndarray
        """
        equation_of_time = pvlibSP.equation_of_time_spencer71(day_of_year)
        solar_hour_angle = pvlibSP.hour_angle(index, self.long, equation_of_time)
        
        return solar_hour_angle
    
    
# Maps the 6 CIE sky types used in PASE to their human-readable category label.
# Types 1, 4, 7, 11, 13 correspond to the 5 zones of the Igawa (2014) Kc/Cle diagram.
# Type 5 (isotropic uniform sky) and any value not in the diagram → "undefined".
SKY_CATEGORY_LABELS = {
    1:  'overcast',
    4:  'intermediate overcast',
    7:  'intermediate',
    11: 'intermediate clear',
    13: 'clear',
}

class Light:
    def __init__(self, WD, SP, sky_type_source='uniform', ghi_multiplier=1):

        self.data = {}
        self.daily_sky_type = {}
        self.sky_type_lut = pd.read_csv(
            static_data_path('Igawa-5_sky_types_lut.csv'), sep=';')

        for year in WD.keys():
            
            GHI = WD[year]['G(h)'].to_numpy() * ghi_multiplier
            
            if isleap(int(year)):
                rad_top_atm = SP.sp_leapY['Top_atm_radiation'].to_numpy()
                apparent_sun_zenith = SP.sp_leapY['apparent_zenith'].to_numpy()
                sun_elevation = SP.sp_leapY['elevation'].to_numpy()
            else:
                rad_top_atm = SP.sp_nonleapY['Top_atm_radiation'].to_numpy()
                apparent_sun_zenith = SP.sp_nonleapY['apparent_zenith'].to_numpy()
                sun_elevation = SP.sp_nonleapY['elevation'].to_numpy()

            n_timesteps = len(sun_elevation)
            kt = self.get_clearness_sky_index(rad_top_atm, GHI)    
            DHI = self.get_diffuse_horizontal_radiation(kt, GHI)
            BHI = self.get_beam_horizontal_radiation(GHI, DHI)
            Ai = self.get_anisotropy_index(rad_top_atm, BHI)
            f = self.get_modulating_factor(GHI, BHI)

            if sky_type_source == 'uniform':
                cie_sky_type = [5]*n_timesteps  # Uniform sky (type 5) * n hourly timesteps in the year

                df = pd.DataFrame({'GHI': GHI.tolist(),
                                   'BHI': BHI.tolist(),
                                   'DHI': DHI.tolist(),
                                   'rad_top_atm': rad_top_atm.tolist(),
                                   'kt': kt.tolist(),
                                   'Ai': Ai.tolist(),
                                   'f': f.tolist(),
                                   'CIE Sky Type': cie_sky_type},
                                  index=WD[year].index)
                n_days = len(WD[year].index.normalize().unique())
                self.daily_sky_type[year] = pd.DataFrame({
                    'CIE Sky Type': pd.Series(5, index=range(n_days)),
                    'Sky Category': pd.Series('undefined', index=range(n_days)),
                })
            else:
                # Compute from weather data
                # Extraterrestrial Normal Irradiance (used for the Kc and Cle below)
                ENI = get_extra_radiation(WD[year].index.dayofyear.values)

                # Meteorological indices Clear Sky Index (Kc) and Cloudless index
                # (Cle) from Igawa (2014)
                Kc = self.get_clear_sky_index(ENI, GHI, apparent_sun_zenith)
                Cle = self.get_cloudless_index(DHI, GHI, sun_elevation)
                cie_sky_type = self.get_sky_type(Kc, Cle)

                df = pd.DataFrame({'GHI': GHI.tolist(),
                                   'BHI': BHI.tolist(),
                                   'DHI': DHI.tolist(),
                                   'rad_top_atm': rad_top_atm.tolist(),
                                   'kt': kt.tolist(),
                                   'Ai': Ai.tolist(),
                                   'f': f.tolist(),
                                   'Kc': Kc.tolist(),
                                   'Cle': Cle.tolist(),
                                   'CIE Sky Type': cie_sky_type},
                                  index=WD[year].index)
                self.daily_sky_type[year] = self.get_daily_sky_type(
                    pd.DataFrame({
                        'CIE Sky Type': pd.Series(cie_sky_type, index=WD[year].index, dtype=float),
                        'GHI': GHI,
                    })
                )
            
            self.data[year] = df
                    
    def get_clearness_sky_index(self, rad_top_atm, GHI):
            
        kt = np.zeros(len(rad_top_atm))    
        kt[rad_top_atm>0] = np.maximum(0.1, np.minimum(GHI[rad_top_atm>0]/rad_top_atm[rad_top_atm>0], 0.9))
        kt[np.where(rad_top_atm<=0)] = 0.1
        
        return kt
    
    def get_diffuse_horizontal_radiation(self, kt, GHI):
        # Erbs et al. correlation (1982), 
        #source : John A. Duffie, William A. Beckman(auth.)- Solar Engineering of Thermal Processes, 
        #Fourth Edition (2013), page 76, equation 2.10.1
        DHI = np.zeros(len(GHI))
        
        ind = np.where(kt<=0.8)
        DHI[ind] = (0.9511*np.ones_like(ind)
                    - 0.1604*kt[ind]
                    + 4.388*kt[ind]**2
                    - 16.638*kt[ind]**3
                    + 12.336*kt[ind]**4)*GHI[ind]
        
        ind = np.where(kt<=0.22)
        DHI[ind] = (np.ones_like(ind) - 0.09*kt[ind])*GHI[ind]

        ind = np.where(kt>0.8)
        DHI[ind] = 0.165*GHI[ind]
        
        return DHI
    
    def get_beam_horizontal_radiation(self, GHI, DHI):
        
        BHI = GHI - DHI
        
        return BHI
    
    def get_anisotropy_index(self, rad_top_atm, BHI):
        #source : John A. Duffie, William A. Beckman(auth.)- Solar Engineering of Thermal Processes, 
        #Fourth Edition (2013), page 92, equation 2.16.3

        Ai = np.zeros(len(rad_top_atm))
        ind = np.where(rad_top_atm!=0)
        Ai[ind] = BHI[ind]/rad_top_atm[ind]
        ind_ai_too_big = np.where(Ai > 1)
        Ai[ind_ai_too_big] = 1

        if len(ind_ai_too_big[0]) > 5:
            print(
                "\u001B[38;5;208mWarning: BHI exceeds top of atmosphere "
                "radiation {0} times : check weather data location and "
                "input location coherence\u001B[0m".format(
                str(len(ind_ai_too_big[0]))))

        return Ai

    def get_modulating_factor(self, GHI, BHI):
        
        f = np.zeros(len(GHI))
        f[GHI>0] = np.sqrt(BHI[GHI>0]/GHI[GHI>0])
        
        return f

    def get_clear_sky_index(self, ENI, GHI, apparent_sun_zenith):

        # Get relative airmass from pvlib method
        # (takes apparent zenith angle in [deg] for Kasten and Young 1989 model)
        m = get_relative_airmass(apparent_sun_zenith, model='kastenyoung1989')

        GHI_non_zero = GHI != 0

        Kc = np.empty(len(GHI))
        Kc[:] = np.nan

        Kc = np.divide(GHI, 0.84 * ENI / m * np.exp(-0.054 * m),
                       out=Kc, where=GHI_non_zero)

        return Kc

    def get_cloudless_index(self, DHI, GHI, sun_elevation):

        sun_elevation_rad = np.deg2rad(sun_elevation)
        GHI_non_zero = GHI != 0

        # Cloud ratio [-]
        Ce = self.get_cloud_ratio(DHI, GHI, GHI_non_zero)

        # Standard cloud ratio [-]
        Ces = (0.08302
               + 0.5358 * np.exp(-17.3 * sun_elevation_rad)
               + 0.3818 * np.exp(-3.2899 * sun_elevation_rad))

        Cle = np.zeros(len(GHI))
        Cle = np.divide((1 - Ce), (1 - Ces), out=Cle, where=GHI_non_zero)

        return Cle

    def get_cloud_ratio(self, DHI, GHI, GHI_non_zero):

        Ce = np.zeros((len(GHI),))

        Ce = np.divide(DHI, GHI, out=Ce, where=GHI_non_zero)

        return Ce

    def get_sky_type(self, Kc_target, Cle_target):

        mydf = pd.DataFrame({'Kc': Kc_target, 'Cle': Cle_target})
        temp_list = []
        for k, v in mydf.iterrows():
            if np.isnan(v['Kc']):
                temp_list.append(np.nan)
            else:
                dist = np.sqrt((self.sky_type_lut['Kc']-v['Kc'])**2 + (self.sky_type_lut['Cle']-v['Cle'])**2)
                i = np.argmin(dist)
                temp_list.append(int(self.sky_type_lut['CIE Sky Type'].iloc[i]))

        return temp_list

    @staticmethod
    def _modal_sky_type_candidates(daytime):
        """Return sky types tied for the highest hourly occurrence count."""
        hour_counts = daytime['CIE Sky Type'].value_counts()
        top_count = hour_counts.iloc[0]
        return hour_counts[hour_counts == top_count].index.tolist()

    @staticmethod
    def _resolve_ghi_tie(daytime, candidates):
        """Among tied sky type candidates, return those with the highest total GHI."""
        ghi_sums = daytime.groupby('CIE Sky Type')['GHI'].sum()
        candidates_ghi = ghi_sums[candidates].sort_values(ascending=False)
        top_ghi = candidates_ghi.iloc[0]
        return candidates_ghi[candidates_ghi == top_ghi].index.tolist()

    @staticmethod
    def _representative_sky_type(day_group):
        """
        Return the representative CIE sky type for a single day.

        Nighttime rows (NaN sky type) are excluded. Ties in hourly count are
        broken by total GHI; remaining ties resolve to the lower sky type number.
        Returns NaN if the entire day is nighttime.
        """
        daytime = day_group.dropna(subset=['CIE Sky Type'])
        if daytime.empty:
            return np.nan

        candidates = Light._modal_sky_type_candidates(daytime)
        if len(candidates) == 1:
            return int(candidates[0])

        ghi_candidates = Light._resolve_ghi_tie(daytime, candidates)
        return int(min(ghi_candidates))

    def get_daily_sky_type(self, df_sky_ghi):
        """
        For each calendar day, pick the most frequent CIE sky type (mode).
        If two types appear equally often, the one with the higher total GHI wins.
        If still tied, the lower sky type number is kept (more conservative choice).
        Nighttime rows (NaN sky type) are ignored throughout.

        Input : DataFrame with columns 'CIE Sky Type' and 'GHI', hourly DatetimeIndex.
        Output: DataFrame indexed by date (365 or 366 rows) with columns
                'CIE Sky Type' (int) and 'Sky Category' (str).
        """
        daily_type = df_sky_ghi.groupby(df_sky_ghi.index.date).apply(self._representative_sky_type)

        daily_category = daily_type.map(
            lambda t: SKY_CATEGORY_LABELS.get(t, 'undefined') if not pd.isna(t) else 'undefined'
        )

        return pd.DataFrame({
            'CIE Sky Type': daily_type,
            'Sky Category': daily_category,
        })


class Sun_positions_sampled:
    
    def __init__(self, lat, long, precision_lvl, loc_name, freq_deter, TZ):
        
        self.lat = lat
        self.long = long
        self.loc_name = loc_name
        self.get_solar_positions_sampled(lat, long, precision_lvl, freq_deter, TZ)
        self.solar_vector = get_sun_vector(self.SP['elevation'], self.SP['azimuth'])
        #self.get_sun_path_diagram()
        #self.get_PVSyst_Plot()
   
    def get_solar_positions_sampled(self, lat, long, precision_lvl, freq_deter, TZ):
        """
        Private method, used to compute the sun positions at an hourly or 1/4 hourly frequence
        for each day (precision = 3), one day per week (precision = 2) or one day per month (precision = 1)

        Returns:
           None
       Attribute SP is a dataframe containing the sun positions at the requested sampling
        """
        if (freq_deter == 8760 or freq_deter == 8784):
            frq = '1h'
            n = 1
        elif (freq_deter == 35040 or freq_deter == 35136):
            frq = '15min'
            n = 4
        elif (freq_deter == 52560 or freq_deter == 52704):
            n = 6
            frq = '10min'
        #Query of the sun positions parameters from pvlib
        index = pd.date_range(start='2005-01-01 00:00', freq=frq, 
                              periods=365*24*n, tz=TZ)
        solar_position = pvlibSP.get_solarposition(index, lat, long)
        
        #Sampling based on required precision level
        if precision_lvl == 1:
            
            SP = solar_position.loc[solar_position.index.day==15]
            
        elif precision_lvl == 2:
            
            SP = solar_position.loc[solar_position.index.day_of_week==3]
            
        else:
            
            SP = solar_position
        #Positions when the sun elevation is below the horizon are discarded to save computation ressources    
        self.SP = SP.loc[SP['elevation']>=0]
        
    def get_sun_path_diagram(self):
        
        #SOURCE : https://pvlib-python.readthedocs.io/en/stable/gallery/solar-position/plot_sunpath_diagrams.html#sphx-glr-gallery-solar-position-plot-sunpath-diagrams-py
       
        fig = plt.figure()        
        ax = plt.subplot(1, 1, 1, projection='polar')
        points = ax.scatter(np.radians(self.SP.azimuth), self.SP.apparent_zenith,
                            s=2, label=None, c=self.SP.doy.round(0))
        ax.figure.colorbar(points)

        self.SP = self.SP.reset_index(level='hour')

        # draw hour labels
        SP_june = self.SP.query("month == 6")
        for h in np.unique(SP_june.hour):
            # choose label position by the smallest radius for each hour
            subset = SP_june.loc[SP_june['hour'] == h]
            r = subset.apparent_zenith
            pos = subset.loc[r.idxmin(),:]
            ax.text(np.radians(pos['azimuth']), pos['apparent_zenith'], str(h))
         
        # draw individual days
        for date in pd.to_datetime(['2019-03-21', '2019-06-21', '2019-12-21']):
            times = pd.date_range(date, date+pd.Timedelta('24h'), freq='5min')
            solpos = pvlibSP.get_solarposition(times, 
                                               self.lat,
                                               self.long)
            solpos = solpos.loc[solpos['apparent_elevation'] > 0, :]
            label = date.strftime('%Y-%m-%d')
            ax.plot(np.radians(solpos.azimuth), solpos.apparent_zenith, label=label)

        ax.figure.legend(loc='upper left')

        # change coordinates to be like a compass
        ax.set_theta_zero_location('N')
        ax.set_theta_direction(-1)
        ax.set_rmax(90)

        fig.savefig(os.path.join('OUTPUTS', 'GRAPHS', 'SunPathDiagram_'+self.loc_name+'.svg'))

    def get_PVSyst_Plot(self):
        
        fig, ax = plt.subplots()
        points = ax.scatter(self.SP.azimuth, self.SP.apparent_elevation, s=2,
                    c=self.SP.doy.round(0), label=None)
        fig.colorbar(points)
        
        SP_june = self.SP.query("month == 6")
        
        for h in np.unique(SP_june.hour):
            # choose label position by the largest elevation for each hour
            subset = SP_june.loc[SP_june['hour'] == h]
            height = subset.apparent_elevation
            pos = subset.loc[height.idxmax(),:]
            ax.text(pos['azimuth'], pos['apparent_elevation'], str(h))

        for date in pd.to_datetime(['2019-03-21', '2019-06-21', '2019-09-21','2019-12-21']):
            times = pd.date_range(date, date+pd.Timedelta('24h'), freq='5min')
            solpos = pvlibSP.get_solarposition(times, 
                                               self.lat,
                                               self.long)
            solpos = solpos.loc[solpos['apparent_elevation'] > 0, :]
            label = date.strftime('%Y-%m-%d')
            ax.plot(solpos.azimuth, solpos.apparent_elevation, label=label)

        ax.figure.legend(loc='upper left')
        ax.set_xlabel('Solar Azimuth (degrees)')
        ax.set_ylabel('Solar Elevation (degrees)')

        plt.show()
        fig.savefig(os.path.join('OUTPUTS', 'GRAPHS', 'PVSystDiagram_'+self.loc_name+'.svg'))




class Ray_casting_scene:
    '''
    Class Light_shade_scene
    
    This class compute the diffuse and direct light using ray casting.
    The ray casting requires point sources from which the ray are casted and direction  
    towards the ray are casted. The point sources are provided by the "Mesh" class.
    For diffuse light, the directions are compute within the class, whilst for direct light
    the directions require the position of the sun which is an input of the class.
    
    
    The class is initiate with a mesh containing the source points and a geometry
    '''
    #The class light shade scene init with a geometry (pyvista.polydata) and a mesh instance
    def __init__(self, mesh, geometry, discrete_sky, diffusers=None, horizon=None):
        self.mesh = mesh
        self.sourcepoints = self.mesh.sourcepoints #center of each cell contained in the mesh

        self.geometry = geometry
        self.n_sourcepoints = self.sourcepoints.shape[0]
        #self.sources_flag_dict = mesh.get_sources_flag_dict()
        self.diffusers=diffusers

        self.discrete_sky = discrete_sky
        self.horizon = horizon
        self.get_diffuse_weights_map()

    def get_light_maps(self, sun_P, visualization=False, Sun_P_map_to_visualize=None):

        if type(self.geometry) == list:
            sv = np.zeros((1,3))
            self.dir_mask = np.zeros((len(self.sourcepoints), len(sun_P[:, 0])))
            self.diffuse_mask = np.zeros((len(self.sourcepoints),
                                          len(sun_P[:, 0]),
                                          len(self.discrete_sky)))

            for time in range(len(sun_P[:,0])):
                print(time)
                geometry = self.geometry[time]
                difff_map = self.get_diffuse_mask(geometry)
                sv[0,:] = sun_P[time,:]
                dirrr_map = self.get_direct_mask(sv, geometry)
                self.dir_mask[:, time] = dirrr_map
                self.diffuse_mask[:, time, :] = difff_map
        else:  # no sun tracking
            self.masks = self.get_mask_from_sky_dir(self.geometry)
            self.diffuse_mask = self.masks['Diffuse']
            self.dir_mask = self.get_direct_mask(sun_P, self.geometry)
            if self.diffusers is not None:
                self.compute_diffuser_map(sun_P)

        if visualization is True:
            self.visualize_direct_light_map(Sun_P_map_to_visualize)
            self.visualize_diffuse_light_map(Sun_P_map_to_visualize)
        else:
            pass

    def self_intercept(self,SourcePoints,intercept_points,id_rays_stopped, id_cells = None,tol = 0.01):
        """
        Private method, used to discard auto-intercept of rays. This can happend when a mesh has been
        done on a geometry (e.g. on a top of a PV). The ray can be intercept nearly at his starting position
        
        Parameters:
            SourcePoints (np.ndarray n x 3): Coordinates of the source points
            intercept_points (np.ndarray n x 3): Coordinates of the interception points
            id_rays_stopped  (list of int): List containing the mapping 
                                            between the source points and the interception points
            tol (float): maximal distance below which an interception is discarded

        Returns:
           cleaned id_rays_stopped list where the auto-interception have been removed
        """
        if id_cells is None:id_cells=np.zeros(id_rays_stopped.shape)
        delta = np.linalg.norm(intercept_points - SourcePoints[id_rays_stopped,:], axis=1)
        id_rays_filt, indices = np.unique(id_rays_stopped[delta>tol], return_index=True)
        return id_rays_filt, id_cells[delta>tol][indices]

    def get_mask_from_sky_dir(self, geometry):
        """
        Create the visualisation matrices for all the different element type in the geometry. One matrix is created per
         type plus one matrix for the diffuse sky (called 'diffuse')

        Parameters:
             geometry: geometry set at the initialization of the instance
        Returns:
              masks[dict]: dictionary containing the  different visualisation matrices.
              The keys correspond to the different element types in the geometry (i.e. 'Diffuse', 'PV', 'Diffuser', etc.)
        """
        masks = {}
        try:
            if geometry.number_of_cells == 0:
                print("Geometry is empty. Returning full diffuse illumination.")
                masks['Diffuse'] = np.ones(self.n_sourcepoints, dtype=np.float16)
                return masks
        except AttributeError:
            if geometry.polydata_all_centrals().number_of_cells == 0:
                print("Geometry is empty. Returning full diffuse illumination.")
                masks['Diffuse'] = np.ones(self.n_sourcepoints, dtype=np.float16)
                return masks
        # Get direction of ray to reach the small suns and compute the sky view of each point
        pTarget = np.column_stack([self.discrete_sky.x,
                                   self.discrete_sky.y,
                                   self.discrete_sky.z])
        n_sky_elements = len(self.discrete_sky)

        # Creation of the source points array (Nx3) with N = len(Source) * len(n_sky_elements)
        SourcePoints = np.repeat(np.column_stack((
            self.sourcepoints[:, 0],
            self.sourcepoints[:, 1],
            self.sourcepoints[:, 2]
        )),
            n_sky_elements,
            axis=0)

        # Creation of the target points array (Nx3) with N = len(Source) * len(n_sky_elements)
        TargetPoints = np.tile(pTarget, [self.n_sourcepoints, 1])

        # Computation of the ray interception of the N rays
        # id_rays_stopped provided the index of the ray which has been intercepted
        try:
            intercept_points, id_rays_stopped, id_intercept_cell = trace_rays(
                geometry.polydata_all_centrals(),
                SourcePoints,
                TargetPoints,
                first_point=False,
                retry=False)
            id_rays_stopped_filtred, id_intercept_cell_filtered = self.self_intercept(SourcePoints, intercept_points,
                                                                                      id_rays_stopped,
                                                                                      id_intercept_cell, tol=0.01)
            hit_object = geometry.polydata_all_centrals().cell_data['Type'][id_intercept_cell_filtered]
            ind_diffuse = np.where(hit_object != 'Diffuser')
            masks['Diffuse'] = np.ones(self.n_sourcepoints * n_sky_elements, bool)
            masks['Diffuse'][id_rays_stopped_filtred[ind_diffuse]] = 0
            masks['Diffuse'] = masks['Diffuse'].reshape(self.n_sourcepoints, n_sky_elements)

            # Loop over types of objects it in the ray casting stage, e.g. "Diffuser"
            # and "PV"
            for object_type in np.unique(hit_object):
                ind = np.where(hit_object == object_type)
                masks[object_type] = np.zeros(self.n_sourcepoints * n_sky_elements, bool)
                masks[object_type][id_rays_stopped_filtred[ind]] = 1
                masks[object_type] = masks[object_type].reshape(self.n_sourcepoints, n_sky_elements)

        except AttributeError:
            intercept_points, id_rays_stopped, id_intercept_cell = trace_rays(
                geometry,
                SourcePoints,
                TargetPoints,
                first_point=False,
                retry=False)

            id_rays_stopped_filtred, _ = self.self_intercept(SourcePoints, intercept_points, id_rays_stopped, tol=0.01)

            masks['Diffuse'] = np.ones(self.n_sourcepoints * n_sky_elements, bool)
            masks['Diffuse'][id_rays_stopped_filtred] = 0

            masks['Diffuse'] = masks['Diffuse'].reshape(self.n_sourcepoints, n_sky_elements)

        if self.horizon is not None and masks['Diffuse'].ndim == 2:
            sky_az = np.asarray(self.discrete_sky.az)
            sky_el = np.asarray(self.discrete_sky.el)
            horizon_vis = np.array(self.horizon.get_horizon_mask(sky_az, sky_el))
            masks['Diffuse'][:] = masks['Diffuse'] & horizon_vis[np.newaxis, :]

        return masks

    def get_diffuse_mask(self, geometry):
        """
        Public method, compute the diffuse light at the point sources defined
        in the input mesh (see this class' constructor) under a
        discrete_sky sky model.

        :param geometry: geometry set at the initialization of the instance
        :return: diffuse_mask: diffuse map binary mask for each source point and discrete_sky element. Shape: (n_sky_patches, n_source_points)

        """

        # Handle empty geometry: return full diffuse light
        try:
            if geometry.polydata_all_centrals().number_of_cells == 0:
                logger.info("Geometry is empty. Returning full diffuse illumination.")
                return np.ones(self.n_sourcepoints, dtype=np.float16)
        except AttributeError:
            if geometry.number_of_cells == 0:
                logger.info("Geometry is empty. Returning full diffuse illumination.")
                diffuse_mask = np.ones(self.n_sourcepoints, dtype=np.float16)

            if self.horizon is not None:
                 sky_az = np.asarray(self.discrete_sky.az)
                 sky_el = np.asarray(self.discrete_sky.el)
                 horizon_vis = np.array(self.horizon.get_horizon_mask(sky_az, sky_el))
                 n_sky_elements = len(self.discrete_sky)
                 full_mask = np.ones((self.n_sourcepoints, n_sky_elements), dtype=bool)
                 full_mask[:] = horizon_vis[np.newaxis, :]
                 return full_mask

            return np.ones((self.n_sourcepoints, len(self.discrete_sky)), dtype=bool)

        #Get direction of ray to reach the small suns and compute the sky view of each point
        pTarget = np.column_stack([self.discrete_sky.x,
                                   self.discrete_sky.y,
                                   self.discrete_sky.z])
        n_sky_elements = len(self.discrete_sky)

        #Creation of the source points array (Nx3) with N = len(Source) * len(n_sky_elements)
        SourcePoints = np.repeat(np.column_stack((
                                                  self.sourcepoints[:,0],
                                                  self.sourcepoints[:,1],
                                                  self.sourcepoints[:,2]
                                                 )),
                                      n_sky_elements,
                                      axis=0)
        
        #Creation of the target points array (Nx3) with N = len(Source) * len(n_sky_elements)
        TargetPoints = np.tile(pTarget,[self.n_sourcepoints,1])

        #Computation of the ray interception of the N rays
        #id_rays_stopped provided the index of the ray which has been intercepted
        try:
            intercept_points, id_rays_stopped, _ = trace_rays(
                geometry.polydata_all_centrals(),
                SourcePoints,
                TargetPoints,
                first_point=False,
                retry=False)

        except AttributeError as e:
            intercept_points, id_rays_stopped, _ = trace_rays(
                geometry,
                SourcePoints,
                TargetPoints,
                first_point=False,
                retry=False)


        id_rays_stopped_filtred, _ = self.self_intercept(SourcePoints,intercept_points,id_rays_stopped,tol = 0.01)

        diffuse_mask = np.ones(self.n_sourcepoints*n_sky_elements, bool)
        diffuse_mask[id_rays_stopped_filtred] = 0

        diffuse_mask = diffuse_mask.reshape(self.n_sourcepoints, n_sky_elements)

        if self.horizon is not None:
            sky_az = self.discrete_sky.az
            sky_el = self.discrete_sky.el
            horizon_vis = np.array(self.horizon.get_horizon_mask(sky_az, sky_el))
            diffuse_mask[:] = diffuse_mask & horizon_vis[np.newaxis, :]

        return diffuse_mask

    def compute_diffuser_map(self, sun_P):
        """
        Compute the map (i.e., the normalized energy reaching the source points)  of the diffusers.
        pTarget (nPatch, 3): the direction of the sky patches
        _cos_elev_patch (nPatch): the cosine of the zenith angle of each patch
        weight (nSolPos, nPatch): weight of each patch for the rays transmitted by the diffusers
        Output:
            diffuser_map (nSolPos, nsourcepoints): the normalized energy reaching the source points from the diffusers
        """
        pTarget = np.column_stack([self.discrete_sky.x,
                                   self.discrete_sky.y,
                                   self.discrete_sky.z])
        _cos_elev_patch = self.discrete_sky['cos(z)']
        weight = self.diffusers.get_light_direction(sun_P, pTarget, self.discrete_sky['Normalized surf area'], np.sqrt(np.min(self.discrete_sky['solid_angle_sr'])))
        self.diffuser_map = np.einsum('ij, j, kj->ik', weight, _cos_elev_patch, self.masks['Diffuser'])

    def get_direct_map_by_flag(self,Flags):
        """
        Public method, filter the computed Direct_Map based on flags
        
        Parameters:
            Flags (list of str): flag used to filter the direct_map
    
        Returns:
           direct_t_map (np.array t x n):  Providing a matrix of boolean (0/1) for each source points (n) and each
                                           sun positions (t). If the point does not directly see the sun a value of 0 is given.
        """

        Index = self.mesh.get_source_points_index(Flags)
        return self.dir_mask[Index, :]

    def get_diffuse_map_by_flag(self,Flags):
        """
        Public method, filter the computed Diffuse_Map based on flags
        
        
        Parameters:
            Flags (list of str): flag used to filter the diffuse_map

        Returns:
           Diffu (np.array 1 x n):  Providing a vector with the fraction ([0-1]) of diffuse light 
                                   for each of the "n" source points defined in the mesh
        """
        
        Index = self.mesh.get_source_points_index(Flags)
        return self.diffuse_mask[Index]

    def get_irradiation_map_by_flag(self,Flags):
        """
        Public method, filter the computed Diffuse_Map based on flags
        
        
        Parameters:
            Flags (list of str): flag used to filter the diffuse_map

        Returns:
            Diffu (np.array 1 x n):  Providing a vector with the fraction ([0-1]) of diffuse light 
                                    for each of the "n" source points defined in the mesh
        """
        
        Index = self.mesh.get_source_points_index(Flags)
        return {y:self.daily_irr_spat[y][:,Index] for y in self.daily_irr_spat}
    
    def get_direct_mask(self, sun_P, geometry):
        """
        Public method, compute the direct light at the point sources defined in the input mesh for
         the positions provide in the sun_P input.
         The method uses the mesh and the geometry set at the initialization of the instance
        
        
        Parameters:
            sun_P (int): sun positions  # TODO the type (int) seems wrong here

        Returns:
           direct_ID_t_map (np.array n x t):  Providing a matrix of boolean (0/1) for each source points (n) and each
                                           sun positions (t). If the point does not directly see the sun a value of 0 is given.
        """

        # Handle empty geometry: return full direct light
        try:
            if geometry.polydata_all_centrals().number_of_cells == 0 :
                n_sun_positions = sun_P.shape[0]
                logger.info("Geometry is empty. Returning full direct illumination.")
                return np.ones((self.n_sourcepoints, n_sun_positions), dtype=np.uint16)
        except:
            if geometry.number_of_cells == 0:
                n_sun_positions = sun_P.shape[0]
                logger.info("Geometry is empty. Returning full direct illumination.")
                direct_mask = np.ones((self.n_sourcepoints, n_sun_positions), dtype=np.uint16)
            
                if self.horizon is not None:
                    
                    beta = np.arcsin(sun_P[:,2])
                    gamma = np.arctan2(sun_P[:,0], sun_P[:,1])
                    
                    # Convert to degrees
                    solar_el = np.degrees(beta)
                    solar_az = np.degrees(gamma)
                    solar_az = np.mod(solar_az, 360) 
                    
                    horizon_vis = self.horizon.is_sun_visible(solar_az, solar_el)
                    direct_mask[:] = direct_mask & horizon_vis[np.newaxis, :]
                    
                return direct_mask

        #Creation of the source points array (Nx3) with N = len(Source) * len(sun_positions)
        SourcePoints = np.repeat(np.column_stack((
                                                  self.sourcepoints[:,0],
                                                  self.sourcepoints[:,1],
                                                  self.sourcepoints[:,2]
                                                 )),
                                      len(sun_P[:,0]),
                                      axis=0)
        
        #Creation of the target points array (Nx3) with N = len(Source) * len(sun_positions)
        TargetPoints = np.tile(sun_P,[self.n_sourcepoints,1])
        
        #Computation of the ray interception of the N rays
        #id_rays_stopped provided the index of the ray which has been intercepted
        try:
            intercept_points, id_rays_stopped, _ = trace_rays(geometry.polydata_all_centrals(), SourcePoints,
                                                         TargetPoints,
                                                         first_point=False,
                                                         retry=False)
        except AttributeError as e:
            intercept_points, id_rays_stopped, _ = trace_rays(
                geometry,
                SourcePoints,
                TargetPoints,
                first_point=False,
                retry=False)

        #Creation of the initial direct map based on the shape of sun_Positions
        direct_1D_map = np.ones(len(TargetPoints[:,0]), dtype=np.uint16)
        
        if len(intercept_points)==0:
            pass
        else:
            id_rays_stopped_filtred, _ = self.self_intercept(SourcePoints,intercept_points,id_rays_stopped,tol = 0.01)
            #Computation of the shade by setting at 0 the locations where rays were intercepted
            direct_1D_map[id_rays_stopped_filtred] = 0
        
        # Apply Horizon Mask if present
        if self.horizon is not None:
             # See logic above for conversion
             beta = np.arcsin(sun_P[:,2]) 
             gamma = np.arctan2(sun_P[:,0], sun_P[:,1])
             solar_el = np.degrees(beta)
             solar_az = np.degrees(gamma)
             solar_az = np.mod(solar_az, 360) 
             
             horizon_vis = self.horizon.is_sun_visible(solar_az, solar_el)
             
             # horizon_vis is (n_time, ). We need to tile it to match (n_source * n_time).
             horizon_mask_1D = np.tile(horizon_vis, self.n_sourcepoints)
             
             # Apply
             direct_1D_map = direct_1D_map & horizon_mask_1D
        
        
    
        
        if type(self.geometry) != list:
        #Reshape of direct map to get a ID,t map
            direct_ID_t_map =  direct_1D_map.reshape(self.n_sourcepoints,
                                                     len(sun_P[:,0]))
        else:
            direct_ID_t_map = direct_1D_map
    
        return direct_ID_t_map

    def get_daily_irradiation_map(self, SP_sampled, light_data,
                                  visualization=False,
                                  year=None, julian_day=None):
        """
        Public method, compute the daily diffuse direct and total irradiation for each location of the input mesh.
        The results are written as an attribute of the class instance
        
        Parameters:
            SP_sampled (df): dataframe with sun positions
            light_data (df): dataframe with meterological data
            visualization (bool): boolean True/False to activate/desactivate the automatic visualization of results
            year (int): year on which visualizing the results of daily irradiation
            julian_day (int): julian day on which visualizing the daily irradiation map

        Returns:
           None
        """
        
        self.daily_irr_spat = {}
        self.daily_dir_irr_spat = {}
        self.daily_diff_irr_spat = {}
        self.daily_diffuser_irr_spat = {}

        #initialisation des différents dataframes utilisés
        #df1 contient les données lié aux positions du soleil utilisé pour les cartes d'ombrage
        #df2 et df3 contiennent les données météos

        #df_1 = SP_sampled.tz_convert('Etc/GMT+0').reset_index()
        dfShade = SP_sampled.tz_localize(None).reset_index()
        dfShade['doy'] = dfShade['index'].dt.dayofyear
        dfShade['RefDate'] = pd.to_datetime(dfShade['index'].dt.date)
        # dfShade['hour'] = dfShade['index'].dt.hour
        dfShade['second'] = pd.to_timedelta(dfShade['index'].dt.time.astype(str)).dt.total_seconds()
        # dfShade['RefHour'] = dfShade['hour']
        dfShade['RefSecond'] = dfShade['second']
        dfShade['SolPosInd'] = np.arange(len(dfShade))

        for year in light_data:
            
            freq_deter = len(light_data[year]['GHI'])
            if (freq_deter == 8760 or freq_deter == 8784):
                n = 1  # n is the number of samples per hour : [h^-1]
            elif (freq_deter == 35040 or freq_deter == 35136):
                n = 4
            elif (freq_deter == 52560 or freq_deter == 52704):
                n = 6
                

            dfWeather = light_data[year].tz_localize(None).reset_index()
            dfWeather['doy'] = dfWeather['index'].dt.dayofyear
            dfWeather['second'] = pd.to_timedelta(dfWeather['index'].dt.time.astype(str)).dt.total_seconds()

            # Jointure entre les données météos et les données d'ombrage en vue de déterminée la date/index liée aux données d'ombrage
            #la plus proche pour chaque donnée météo
            dfWeatherMerged = pd.merge_asof(dfWeather,dfShade[['RefDate','doy']],on=['doy'],direction='nearest',suffixes=('_x','_y')).sort_values('second')
            irradianceMap_direct = {}
            irradianceMap_diffus = {}
            irradianceMap_diffuser = {}

            # Loop over days of year (doy) to compute daily irradiance.
            doy = dfWeatherMerged['index'].dt.dayofyear.unique()
            doy.sort()  # doy = [1, 2, 3, ..., 365]
            if type(self.geometry)==list:
                temp_list = []
            for day in doy:
                
                #Create sub-dataframe of day number "day"'s data
                sublight_df = dfWeatherMerged.loc[dfWeatherMerged['index'].dt.dayofyear==day,:]
                df_subShade = dfShade.loc[sublight_df['RefDate'].unique()[0]==dfShade['RefDate']]
                df_subShade_merged = df_subShade.join(sublight_df[['second','BHI','GHI','DHI']].set_index('second'),on='second',how='left',rsuffix='_',lsuffix="__")
                
                #df_subShade_merged = df_subShade.join(sublight_df[['hour','BHI','GHI','DHI']].set_index('hour'),on='hour',how='left',rsuffix='_',lsuffix="__")
                #Join on closest instant of the day based on seconds elapsed since start of day
                df_subShade_merged = pd.merge_asof(df_subShade,sublight_df[['second','BHI','GHI','DHI','doy', 'CIE Sky Type']].set_index('second'),on=['second'],direction='nearest',suffixes=('_x','_y')).sort_values('second')

                # Compute direct and diffuse irradiance on that day and store in the dict
                irradianceMap_direct[day] = np.sum(self.dir_mask[:, list(df_subShade.index)] * df_subShade_merged['BHI'].to_numpy(), axis=1) * 10 ** -6 * 60 * 60 / n
                if type(self.geometry) != list:  # no sun tracking
                    irradianceMap_diffus[day] = self.compute_daily_diff_irradiation(df_subShade_merged.dropna(), n)
                    indices = np.where(dfWeatherMerged['index'].dt.dayofyear == day)[0]
                    if self.diffusers is not None:
                        irradianceMap_diffuser[day] = self.compute_daily_diffuser_irradiation(df_subShade_merged.dropna(), n)
                    else:
                        irradianceMap_diffuser[day] = np.zeros(irradianceMap_diffus[day].shape)
                else:  # sun tracking -> in this case, the mask changes at each time step
                    PASE_Logger(f'{day=}', level='DEBUG')
                    PASE_Logger(f'{df_subShade.index=}', level='DEBUG')
                    # irradianceMap_diffus[day] = np.sum(df_subShade_merged['DHI'].to_numpy()
                    #                                    * self.diffuse_mask[:, list(df_subShade.index)], axis=1) * 10 ** -6 * 60 * 60 / n

                    # The following takes too long, as the loop advances the irradianceMap_diffus
                    # grows and slows down execution
                    # irradianceMap_diffus[
                    #     day] = self.compute_daily_diff_irradiation(
                    #     df_subShade_merged.dropna(), n)

                    # The "rows_to_keep" is used to keep only rows with
                    # non-zero DHI values in the df_subShade.index, used in
                    # self.compute_daily_diff_irradiation() below.
                    rows_to_keep = ~ df_subShade_merged['CIE Sky Type'].isna().values

                    # TODO check this new syntax : is it more efficient ?
                    temp_list.append(self.compute_daily_diff_irradiation(
                        df_subShade_merged.dropna(), n, df_subShade.index[rows_to_keep]))

            if type(self.geometry) == list:
                irradianceMap_diffus = dict(zip(doy, temp_list))

                if self.diffusers is None:
                    irradianceMap_diffuser[day] = np.zeros(
                        irradianceMap_diffus[day].shape)

            # Convert dictionaries to Numpy arrays and add to the ad-hoc attribute
            self.daily_irr_spat[year] = (pd.DataFrame.from_dict(irradianceMap_diffus).to_numpy()
                                         + pd.DataFrame.from_dict(irradianceMap_direct).to_numpy()
                                         + pd.DataFrame.from_dict(irradianceMap_diffuser).to_numpy())
            self.daily_dir_irr_spat[year] = pd.DataFrame.from_dict(irradianceMap_direct).to_numpy()    
            self.daily_diff_irr_spat[year] = pd.DataFrame.from_dict(irradianceMap_diffus).to_numpy()
            self.daily_diffuser_irr_spat[year] = pd.DataFrame.from_dict(irradianceMap_diffuser).to_numpy()

        if visualization == True:
            self.visualize_daily_irrad_map(year, julian_day)
        else:
            pass

    def get_diffuse_weights_map(self):
        """
        Build a diffuse weighting map by :
        - sky patch area
        - cos(z)
        """
        diffuse_weights_map = self.discrete_sky['cos(z)'].values * self.discrete_sky['Normalized surf area'].values
        self.normalized_diffuse_weights_map = diffuse_weights_map/diffuse_weights_map.sum()

    def get_diffuse_shaded_weights_map(self):
        mask = self.diffuse_mask
        norm = np.asarray(self.normalized_diffuse_weights_map, dtype=np.float64)
        ndim = mask.ndim

        if ndim == 1:
            self.diffuse_shaded_weights_map = norm[np.newaxis, :] * mask[:, np.newaxis]
            return

        if ndim == 2:
            self.diffuse_shaded_weights_map = norm * mask
            return

        # ndim == 3, mask (Nsourcepoints, Ntimesteps, Nskypatches)
        self.diffuse_shaded_weights_map = norm * mask

    def get_shaded_radiance_contrib(self, az, el, sky_type):
        radiance_distr = CIEStandardSky(self.discrete_sky,
                                             az,
                                             el,
                                             sky_type).rel_radiance_distribution

        self.get_diffuse_shaded_weights_map()

        mask = self.diffuse_shaded_weights_map  # no tracking: (nSourcePoints, nSkyPatches) ; tracking: (nSourcePoints, Ntimesteps, nSkyPatches)
        rd = radiance_distr.astype(mask.dtype, copy=False)

        if mask.ndim == 1:  # No panels
            # elementwise multiply -> same length
            # rd: shape (Nskypatches,)
            # mask: shape (Nsourcepoints,)
            # goal: result shape (Nsourcepoints, Nskypatches)
            return np.multiply(mask[:, None], rd[None, :])

        if type(self.geometry) == list:  # sun tracking
            # There should be either the case ndim == 2 or 3, but not both I think
            if mask.ndim == 2:
                # mask shape (Nskypatches, Nsourcepoints)
                # goal: result shape (Nskypatches, Nsourcepoints)
                # preallocate
                res = np.empty_like(mask, dtype=rd.dtype)
                # broadcast radiance over axis 1
                res[:] = rd[:, None]
                res *= mask
                return res
            elif mask.ndim == 3:
                # mask shape (nSourcePoints, Ntimesteps, nSkyPatches)
                res = np.empty_like(mask, dtype=rd.dtype)
                res[:] = rd[None, None, :]
                res *= mask
                return res
        else:
            # mask shape is (Nsourcepoints, Nskypatches)
            norm = np.asarray(self.normalized_diffuse_weights_map, dtype=rd.dtype)
            sky_integral = (rd * norm).sum()  # sky_integral used to normalize the resulting shaded radiance distribution
            res = np.empty_like(mask, dtype=rd.dtype)
            res[:] = rd[None, :]
            res *= mask
            res /= sky_integral  # normalize the resulting radiance distribution
            # res shape is (Nsourcepoints, Nskypatches)
            return res

    def compute_daily_diff_irradiation(self, df, n_freq, indices=None):
        """
        Compute the daily irradiation received by each source points from the sky (diffuse irradiation).
        Input:
            df (pandas DataFrame of size T): Weather data with at least 'GHI' and 'SolPosInd' columns.
            n_freq (int) : number of samples per hours
            indices (Mask of bool values) indices of the rows where CIE sky is a number
        Output:
            diff_irradiance_map_MJ_m2: daily diffuse irradiance in MJ/m²
        """
        dhi = df['DHI'].to_numpy()  # (T,)
        az = df['azimuth'].to_numpy()  # (T,)
        el = df['elevation'].to_numpy()  # (T,)
        sky_type = df['CIE Sky Type'].to_numpy()  # (T,)
        T = dhi.shape[0]

        outs = [self.get_shaded_radiance_contrib(az[i], el[i], sky_type[i]) for i in range(T)]

        # Determine if outputs are 2D or 3D per-time and stack appropriately
        if outs[0].ndim == 2:  # No tracking
            stacked = np.stack(outs, axis=0)   # (T, M, P) if each out is (M,P)
            weighted = stacked * dhi[:, None, None]
            diff_irradiance_map = weighted.sum(axis=(0,2))  # returns (nSourcePoints,) ndarray
        elif outs[0].ndim == 3:  # ndim==3; Tracking active
            if indices is None:
                raise ValueError("indices required")
            stacked = np.stack(outs, axis=0)   # (nHours, nSourcePoints, nSunPositions, Nskypatches) ; nHours is the number of hours with sunlight during the day that is being computed
            t_idx = np.arange(T)
            selected = stacked[t_idx, :, indices, :]
            weighted = selected * dhi[:, None, None]
            diff_irradiance_map = weighted.sum(axis=(0, 2))
        elif outs[0].ndim == 1: # for unity benchmarking case (only ?)
            stacked = np.stack(outs, axis=0)
            weighted = stacked * dhi[:, None, None]
            diff_irradiance_map = weighted.sum(axis=-1)

        diff_irradiance_map_MJ_m2 = diff_irradiance_map * 3600.0 * 1e-6 / n_freq
        return diff_irradiance_map_MJ_m2  # shape (nSourcePoints,)

    def  compute_daily_diffuser_irradiation(self, df, n_freq):
        """
        Compute the daily irradiation received by each source points from the diffusers.
        Input:
            df (pandas DataFrame of size T): Weather data with at least 'GHI' and 'SolPosInd' columns.
            n_freq (int) : number of samples per hours
        Output:
            diffuser_irradiance_map_MJ_m2: daily irradiance from the diffusers in MJ/m²
        """
        ghi = df['GHI'].to_numpy()  # (T,)
        ghi = ghi[:, np.newaxis]
        indices = df['SolPosInd']
        outs = self.diffuser_map[indices,:]
        weighted = ghi*outs
        diffuser_irradiance_map = weighted.sum(axis=0)
        diffuser_irradiance_map_MJ_m2 = diffuser_irradiance_map * 3600.0 * 1e-6 / n_freq
        return diffuser_irradiance_map_MJ_m2

    def visualize_direct_light_map(self, Sun_P_map_to_visualize):
        """
        Open the visualization of the direct light map for a specific
        sun position corresponding to the sun positions sampled vector

        Parameters
        ----------
        Sun_P_map_to_visualize : integer
            id of the sun position in the sun positions sampled vector

        Returns
        -------
        None.

        """

        if type(self.geometry) == list:
            geo = self.geometry[Sun_P_map_to_visualize]
        else:
            geo = self.geometry

        if self.horizon is not None:
             h_mesh = self.horizon.get_visualization_mesh()
        else:
             h_mesh = None

        open_pyvista_3D_visualization(self.sourcepoints[:, :],
                                      self.dir_mask[:, Sun_P_map_to_visualize],
                                      geo,
                                      "Direct map [-]",
                                      extra_mesh=h_mesh)

    def visualize_diffuse_light_map(self, Sun_P_map_to_visualize):
        """
        Open the visualization of the diffuse light map for a specific
        tilt of the PV modules if there is a rotation axis
        (corresponding to a sun position from the sun positions sampled vector)

        Parameters
        ----------
        Sun_P_map_to_visualize : integer
            id of the sun position in the sun positions sampled vector

        Returns
        -------
        None.

        """

        if type(self.geometry) == list:
            geo = self.geometry[Sun_P_map_to_visualize]
            diffuse_shaded_weights_map = self.diffuse_shaded_weights_map[:,
                                         Sun_P_map_to_visualize, :]
        else:
            geo = self.geometry
            diffuse_shaded_weights_map = self.diffuse_shaded_weights_map

        try:
            map_to_display = np.array(diffuse_shaded_weights_map.sum(axis=1))
        except np.exceptions.AxisError as e:
            print(f'{e} ; \n Other shape ...')
            map_to_display = np.array(diffuse_shaded_weights_map)


        if self.horizon is not None:
             h_mesh = self.horizon.get_visualization_mesh()
        else:
             h_mesh = None

        open_pyvista_3D_visualization(self.sourcepoints[:, :],
                                      np.array(map_to_display, dtype=np.float32),
                                      geo,
                                      "Unweighted shaded diffuse map [-]",
                                      extra_mesh=h_mesh)

    def visualize_diffuser_light_map(self, Sun_P_map_to_visualize, sun_P):
        """
        Open the visualization of the diffuser light map for a specific
        tilt of the PV modules if there is a rotation axis
        (corresponding to a sun position from the sun positions sampled vector)

        Parameters
        ----------
        Sun_P_map_to_visualize : integer
            id of the sun position in the sun positions sampled vector

        Sun_P : array Nx3
            sun positions sampled vector

        Returns
        -------
        None.

        """
        labels = dict(zlabel='Z (ZENITH)', xlabel='X (EAST)', ylabel='Y (NORTH)')

        plotter = pyV.Plotter()

        plotter.add_mesh(self.geometry.polydata_by_property(property_dict={'Type':['PV']}), color='black')
        plotter.add_mesh(self.geometry.polydata_by_property(property_dict={'Type':['Diffuser']}), color='skyblue')
        ground = np.array([[-200, 200, 0],
                           [200, 200, 0],
                           [-200, -200, 0],
                           [200, -200, 0]])

        ground_m = np.hstack([[3, 0, 1, 2],
                              [3, 1, 2, 3], ])

        grnd = pyV.PolyData(ground, ground_m)

        plotter.add_mesh(grnd, color='green')

        plotter.add_axes(**labels)

        plotter.add_mesh(self.sourcepoints[:, :],
                         scalars=np.array(self.diffuser_map[Sun_P_map_to_visualize,:], dtype=np.float32),
                         point_size=10,
                         lighting=False,
                         show_edges=False,
                         scalar_bar_args={"title": 'Diffuser map'},
                         clim=[np.array(self.diffuser_map[Sun_P_map_to_visualize,:], dtype=np.float32).min(),
                               np.array(self.diffuser_map[Sun_P_map_to_visualize,:], dtype=np.float32).max()])
        D = self.geometry.polydata_by_property(property_dict={'Type':['Diffuser']}).center_of_mass()
        plotter.add_lines(np.array([D, D+sun_P[Sun_P_map_to_visualize]]), color='yellow', width=1)
        plotter.add_lines(np.array([D, D + self.diffusers.normal]), color='black',width=1)
        plotter.add_lines(np.array([D, D + self.diffusers.len_vector]), color = 'black', width = 1)
        dr = 3*np.array([self.diffusers.x_sr[Sun_P_map_to_visualize, :],
                       self.diffusers.y_sr[Sun_P_map_to_visualize, :],
                       self.diffusers.z_sr[Sun_P_map_to_visualize, :],
                       ])
        dr = dr.T
        N = dr.shape[0]
        points = np.vstack([np.repeat(D[None, :], N, axis=0), D - dr])

        lines = np.hstack([[2, i, i + N] for i in range(N)])
        poly = pyV.PolyData(points, lines=lines)

        plotter.add_mesh(poly, color='red', line_width=1)
        plotter.show()

    def visualize_diffuser_light_map(self, Sun_P_map_to_visualize, sun_P):
        """
        Open the visualization of the diffuser light map for a specific
        tilt of the PV modules if there is a rotation axis
        (corresponding to a sun position from the sun positions sampled vector)

        Parameters
        ----------
        Sun_P_map_to_visualize : integer
            id of the sun position in the sun positions sampled vector

        Sun_P : array Nx3
            sun positions sampled vector

        Returns
        -------
        None.

        """
        labels = dict(zlabel='Z (ZENITH)', xlabel='X (EAST)', ylabel='Y (NORTH)')

        plotter = pyV.Plotter()

        plotter.add_mesh(self.geometry.polydata_by_property(property_dict={'Type':['PV']}), color='black')
        plotter.add_mesh(self.geometry.polydata_by_property(property_dict={'Type':['Diffuser']}), color='skyblue')
        ground = np.array([[-200, 200, 0],
                           [200, 200, 0],
                           [-200, -200, 0],
                           [200, -200, 0]])

        ground_m = np.hstack([[3, 0, 1, 2],
                              [3, 1, 2, 3], ])

        grnd = pyV.PolyData(ground, ground_m)

        plotter.add_mesh(grnd, color='green')

        plotter.add_axes(**labels)

        plotter.add_mesh(self.sourcepoints[:, :],
                         scalars=np.array(self.diffuser_map[Sun_P_map_to_visualize,:], dtype=np.float32),
                         point_size=10,
                         lighting=False,
                         show_edges=False,
                         scalar_bar_args={"title": 'Diffuser map'},
                         clim=[np.array(self.diffuser_map[Sun_P_map_to_visualize,:], dtype=np.float32).min(),
                               np.array(self.diffuser_map[Sun_P_map_to_visualize,:], dtype=np.float32).max()])
        D = self.geometry.polydata_by_property(property_dict={'Type':['Diffuser']}).center_of_mass()
        plotter.add_lines(np.array([D, D+sun_P[Sun_P_map_to_visualize]]), color='yellow', width=1)
        plotter.add_lines(np.array([D, D + self.diffusers.normal]), color='black',width=1)
        plotter.add_lines(np.array([D, D + self.diffusers.len_vector]), color = 'black', width = 1)
        dr = 3*np.array([self.diffusers.x_sr[Sun_P_map_to_visualize, :],
                       self.diffusers.y_sr[Sun_P_map_to_visualize, :],
                       self.diffusers.z_sr[Sun_P_map_to_visualize, :],
                       ])
        dr = dr.T
        N = dr.shape[0]
        points = np.vstack([np.repeat(D[None, :], N, axis=0), D - dr])

        lines = np.hstack([[2, i, i + N] for i in range(N)])
        poly = pyV.PolyData(points, lines=lines)

        plotter.add_mesh(poly, color='red', line_width=1)
        plotter.show()

    def visualize_daily_irrad_map(self, year, julian_day):
        """
        Open the visualization of the daily irradiation map
        for a specific year and julian day

        Parameters
        ----------
        year : integer
            year on which to visualize daily irradiation
        julian_day : integer
            julian day on which to visualize daily irradiation

        Returns
        -------
        None.

        """

        if type(self.geometry) == list:
            geo = self.geometry[0]
        else:
            geo = self.geometry

        if self.horizon is not None:
             h_mesh = self.horizon.get_visualization_mesh()
        else:
             h_mesh = None

        open_pyvista_3D_visualization(self.sourcepoints[:, :],
                                      self.daily_irr_spat[str(year)][:,
                                      julian_day],
                                      geo,
                                      "Total irradiation reaching the ground on the julian day " + str(
                                          julian_day) + " of " + str(
                                          year) + " [MJ/m²]",
                                      extra_mesh=h_mesh)

