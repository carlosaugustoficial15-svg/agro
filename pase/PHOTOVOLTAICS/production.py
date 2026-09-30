#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#Copyright (c) 2020-2024 - University of Liège - Digital Energy and Agriculture Lab (DEAL)
#Author : Roxane Bruhwyler (roxane.bruhwyler@uliege.be or roxane.bruhwyler@hotmail.com)
#This file is part of the PASE software, and is distributed under the MIT license.

import logging
import numpy as np
import pandas as pd
import os
import pvlib

logger = logging.getLogger(__name__)


def _studio_progress(message):
    if os.environ.get("PASE_STUDIO") == "1":
        print(f"PASE Studio: {message}", flush=True)

class PV_Production:
    
    def __init__(self, inputs, electrical_model="simple", module_parameters=None):
        
        self.bifaciality = inputs['Bifaciality']
        self.bifaciality_factor = inputs['Bifaciality_factor']
        self.azimut = inputs['CentralAzimut']*np.pi/180
        panel_peak_power = inputs['Panel_Peak_Power']
        self.panel_area = inputs['PanelDimensionX']*inputs['PanelDimensionY']
        self.panel_efficiency = panel_peak_power/(self.panel_area*1000)
        self.n_panels = (inputs['NumberOfPanelsX']*inputs['NumberOfPanelsY']*
                         inputs['NumberOfPVBlocksX']*inputs['NumberOfPVBlocksY'])
        self.electrical_model = electrical_model
        self.module_parameters = module_parameters or {}
        
        
        self.n_rot_axis = inputs['RotationAxisNumber']
        self.tiltY = inputs['TiltY']
        
        #Temporary line
        #self.slope_in_rot_axis_direction = 0
        self.soil_angle = 0
        self.block_dim_x = (inputs['RepetitionDistanceOfPanelsX']*inputs['NumberOfPanelsX']
                            -inputs['RepetitionDistanceOfPanelsX']
                            +inputs['PanelDimensionX'])
        
        self.NumberOfPanelsX=inputs['NumberOfPanelsX']
        self.RepetitionDistanceOfPanelsX=inputs['RepetitionDistanceOfPanelsX']
        
        self.block_space_x = inputs['RepetitionDistanceOfPVBlocksX']
        
        self.GCR_x = (inputs['PanelDimensionX']*inputs['NumberOfPanelsX']/
                      self.block_space_x)

    def get_several_years_of_electricity_production(self, SP, light, WD,
                                                    albedo_file=None,
                                                    albedo_option=1,
                                                    albedo_default_value=0.2):

        self.production = {}

        albedo = albedo_default_value  # Default constant value

        starting_year = min(light.keys())
        ending_year = max(light.keys())
        freq = SP.sp_leapY.index.freq
        albedo_nyears = 0
        if albedo_option == 2:  # Albedo time series
            albedo_nyears = self.get_n_years_albedo_from_csvfile(albedo_file,
                                                                 starting_year,
                                                                 ending_year,
                                                                 freq,
                                                                 albedo_default_value)

        for year in light.keys():
            _studio_progress(f"produção elétrica {year}: posição solar")
            
            if int(year)%4 == 0:
                sun_vect = SP.sun_vect_leapY
                app_zenith = SP.sp_leapY['apparent_zenith'].to_numpy()
                
            else:
                sun_vect = SP.sun_vect_nonleapY
                app_zenith = SP.sp_nonleapY['apparent_zenith'].to_numpy()

            if albedo_option == 2:  # Albedo time series
                albedo = albedo_nyears[year].Albedo.values

            tiltY, sv_CC = self.get_tiltY_along_time(sun_vect)
            _studio_progress(f"produção elétrica {year}: sombreamento geométrico")
            SF_front = self.get_shading_factor_front(sv_CC, tiltY)
            _studio_progress(f"produção elétrica {year}: sombreamento frontal")
            SF_rear =self.get_shading_factor_rear(sv_CC, tiltY)
            _studio_progress(f"produção elétrica {year}: sombreamento traseiro")

            # Improved ground-transmitted GHI based on ground coverage ratio
            ground_coverage_ratio = self.get_ground_coverage_ratio(tiltY)
            GHI_reaching_ground = light[year]['GHI'].to_numpy() * (1.0 - ground_coverage_ratio)

            GTI_front, GTI_rear = self.get_GTI(sun_vect, app_zenith, 
                                               light[year], GHI_reaching_ground,
                                               albedo, SF_front, SF_rear, tiltY)
            _studio_progress(f"produção elétrica {year}: irradiância nos módulos")
            
            ws = WD[year]['WS10m'].to_numpy()
            amb_temp = WD[year]['T2m'].to_numpy()
            
            panels_temp = self.get_panels_temperature(ws, amb_temp,
                                                      GTI_front+GTI_rear)
            _studio_progress(f"produção elétrica {year}: temperatura dos módulos")
            
            frontP_panel, rearP_panel, P_central = self.get_power_production(
                panels_temp, GTI_front, GTI_rear)
            _studio_progress(f"produção elétrica {year}: concluída")
            
            df = pd.DataFrame({'GTI_f': GTI_front.tolist(),
                               'GTI_r': GTI_rear.tolist(),
                               'panels_T': panels_temp.tolist(),
                               'SF_f': SF_front.tolist(),
                               'SF_r': SF_rear.tolist(),   
                               'front_P_panel': frontP_panel.tolist(),
                               'rear_P_panel': rearP_panel.tolist(),
                               'P_central': P_central.tolist()}, 
                               index=WD[year].index)
            
            self.production[year] = df 

    def get_power_production(self, panels_T, GTI_front, GTI_rear, alpha=-0.4, T_std=25):
        
        one = np.ones((len(panels_T)))
        
        if self.electrical_model == "cec":
            params = self.module_parameters
            missing = [key for key in ("alpha_sc", "a_ref", "I_L_ref", "I_o_ref", "R_sh_ref", "R_s", "Adjust") if params.get(key) is None]
            if missing:
                raise ValueError("Parâmetros CEC ausentes: " + ", ".join(missing))
            total_irradiance = GTI_front + GTI_rear * self.bifaciality_factor
            common = {key: params[key] for key in ("alpha_sc", "a_ref", "I_L_ref", "I_o_ref", "R_sh_ref", "R_s", "Adjust")}
            common.update({"EgRef": params.get("EgRef", 1.121), "dEgdT": params.get("dEgdT", -0.0002677)})
            def maximum_power(irradiance):
                irradiance = np.asarray(irradiance, dtype=float)
                diode = pvlib.pvsystem.calcparams_cec(np.maximum(irradiance, 1e-6), panels_T, **common)
                power = np.asarray(pvlib.pvsystem.singlediode(*diode)["p_mp"], dtype=float)
                return np.where(irradiance > 0, power, 0.0)
            front_power_panel = maximum_power(GTI_front)
            total_power_panel = maximum_power(total_irradiance)
            rear_power_panel = np.maximum(total_power_panel - front_power_panel, 0.0)
            return front_power_panel, rear_power_panel, total_power_panel * self.n_panels * 1e-6

        front_power_panel = (self.panel_efficiency*GTI_front
                                  *(1+((alpha/100)*(panels_T-T_std*one)))
                                  *self.panel_area) # W
        
        rear_power_panel = (self.panel_efficiency*self.bifaciality_factor
                                 *GTI_rear
                                 *(1+((alpha/100)*(panels_T-T_std*one)))
                                 *self.panel_area) # W
                                   
        power_central = ((front_power_panel + rear_power_panel)
                              *self.n_panels*(10**-6))  # MW    
        
        return front_power_panel, rear_power_panel, power_central
            
    def get_GTI(self, sun_vect, app_zenith, light, GHI_reaching_ground,
                albedo, SF_f, SF_r, tiltY):
        
        self.cos_teta = self.get_cos_angle_btw_light_and_panels_normal(sun_vect,
                                                                       [0,0,1],
                                                                       tiltY)
        cos_teta_z = self.get_cos_angle_btw_light_and_zenith(app_zenith)
        
        self.Rb = self.get_ratio_beam_radiation(self.cos_teta, cos_teta_z)
        
        GTI_front = self.compute_global_tilted_irradiance(self.Rb,
                                                          GHI_reaching_ground,
                                                          albedo,
                                                          light['BHI'].to_numpy(),
                                                          light['DHI'].to_numpy(),
                                                          light['Ai'].to_numpy(),
                                                          light['f'].to_numpy(),
                                                          SF_f,
                                                          tiltY)
        
        if self.bifaciality == 1:
            
            self.cos_teta_rear = self.get_cos_angle_btw_light_and_panels_normal(
                sun_vect, [0,0,-1], tiltY)
            
            self.Rb_rear = self.get_ratio_beam_radiation(self.cos_teta_rear, cos_teta_z)
            
            GTI_rear = self.compute_global_tilted_irradiance(self.Rb_rear,
                                                             GHI_reaching_ground,
                                                             albedo,
                                                             light['BHI'].to_numpy(),
                                                             light['DHI'].to_numpy(),
                                                             light['Ai'].to_numpy(),
                                                             light['f'].to_numpy(),
                                                             SF_r,
                                                             tiltY)
            
        else:
            GTI_rear = np.zeros(len(sun_vect))
            
        return GTI_front, GTI_rear
            
    def compute_global_tilted_irradiance(self, Rb, GHI_ground, albedo, BHI,
                                         DHI, Ai, f, SF, tiltY):
        """

        Args:
            Rb: TODO
            GHI_ground: GHI reaching the ground [W/m²]
            albedo: albedo [-]
            BHI: Beam Horizontal Irradiance [W/m²]
            DHI: Diffuse Horizontal Irradiance [W/m²]
            Ai: Anisotropy index
            f: modulating factor
            SF: ? TODO
            tiltY: panel inclination [?] TODO

        Returns:
            GTI: Global Tilted Irradiance [W/m²]
        """
        one = np.ones((len(Ai)))
        zero_vector = np.zeros((len(Ai)))  # TODO   remove ? this is unused
        
        tilt = tiltY*np.pi/180
        
        direct_component = (BHI + DHI*Ai)*Rb*(one - SF)
        
        diffuse_component = (DHI*(one - Ai)*((one + np.cos(tilt))/2)
                                  *(one + f*(np.sin(tilt/2))**3))    
        
        reflected_component = (GHI_ground*albedo*(one-np.cos(tilt))/2)   
        
        GTI = direct_component + diffuse_component + reflected_component
        
        return GTI
    
    def get_cos_angle_btw_light_and_panels_normal(self, sun_vect, 
                                                  init_panel_normal,
                                                  tiltY):    
        vectors = np.asarray(sun_vect, dtype=float)
        tilt = np.asarray(tiltY, dtype=float)
        if tilt.ndim == 0:
            tilt = np.full(len(vectors), float(tilt))
        tilt = np.deg2rad(tilt)
        normal = np.broadcast_to(np.asarray(init_panel_normal, dtype=float), vectors.shape)

        # Equivalent to rotating the normal about Y by tilt and then about Z
        # by -azimuth. This avoids SciPy's native Rotation path, which aborts
        # the bundled Windows runtime on leap-year vectors.
        ct, st = np.cos(tilt), np.sin(tilt)
        x_tilt = ct * normal[:, 0] + st * normal[:, 2]
        y_tilt = normal[:, 1]
        z_tilt = -st * normal[:, 0] + ct * normal[:, 2]
        ca, sa = np.cos(self.azimut), np.sin(self.azimut)
        x_normal = ca * x_tilt + sa * y_tilt
        y_normal = -sa * x_tilt + ca * y_tilt
        return x_normal * vectors[:, 0] + y_normal * vectors[:, 1] + z_tilt * vectors[:, 2]
    
    def get_cos_angle_btw_light_and_zenith(self, app_zenith):
        
        cos_teta_z = np.cos(app_zenith*np.pi/180)
        
        return cos_teta_z
    
    def get_ratio_beam_radiation(self, cos_teta, cos_teta_z):
        #source : John A. Duffie, William A. Beckman(auth.)- Solar Engineering of Thermal Processes, 
        #Fourth Edition (2013), page 23, equation 1.8.1
        Rb = cos_teta/cos_teta_z
        ind1 = np.where(cos_teta<=0)
        Rb[ind1] = 0
        ind2 = np.where(cos_teta_z<=0)
        Rb[ind2] = 0
        Rb[Rb>25] = 25
        
        return Rb

    def get_panels_temperature(self, WS, T, tot_GTI):
        
        #Source : Thermal lost in PVsyst (https://www.pvsyst.com/help/thermal_loss.htm)
        
        Uc = 25 # [W/m².K] 
        Uv = 1.2  # [(W/(m².K))/(m/s)]
        alpha = 0.9  # absorption coefficient
        U = Uc*np.ones((len(WS))) + Uv*WS
        
        panels_temp = T + 1/U*(alpha*tot_GTI*(1-self.panel_efficiency))
        
        return panels_temp
    
    def get_tiltY_along_time(self, sun_vect):
        
        if self.n_rot_axis == 0:
            
            tiltY = self.tiltY*np.ones((len(sun_vect[:,0])))             
            sun_vect_central_coord = self.get_sun_vect_in_central_coord(sun_vect)
                        
        elif self.n_rot_axis == 1:
            
            sun_vect_central_coord = self.get_sun_vect_in_central_coord(sun_vect)
            true_tracking_angle = self.get_true_tracking_angle(sun_vect_central_coord)
            backT_corr_angle = self.get_backT_corr_angle(true_tracking_angle)
            tiltY_corrected = self.get_corrected_tracking_angle(true_tracking_angle,
                                                                 backT_corr_angle)
            tiltY_limited = self.get_limitated_angle(tiltY_corrected)
            tiltY = tiltY_limited*180/np.pi
            
        return tiltY, sun_vect_central_coord
    def get_ground_coverage_ratio(self, tiltY_deg):

        #Ground coverage ratio (fraction of ground covered by the projection of PV panels), computed at each instant.
        tilt_rad = np.deg2rad(tiltY_deg)
        # Projection effect along X (rotation around Y): projected length scales with cos(tilt)
        coverage = self.GCR_x * np.cos(tilt_rad)

        return coverage
    
    def get_sun_vect_in_central_coord(self, sun_vect):
        # Do not take into account the slope of the area and the slope of the 
        # rotation axis (see the previous framework to complete)
        sun_vect_CC = np.zeros((len(sun_vect[:,0]),3))
            
        sun_vect_CC[:,0] = sun_vect[:,0]*np.cos(self.azimut)\
            - sun_vect[:,1]*np.sin(self.azimut)                                               
                                                     
        sun_vect_CC[:,1] = sun_vect[:,0]*np.sin(self.azimut)\
            + sun_vect[:,1]*np.cos(self.azimut)
                                                       
        sun_vect_CC[:,2] = sun_vect[:,2]
        
        return sun_vect_CC
    
    def get_true_tracking_angle(self, sun_v_central_coord):
                    
        true_tracking_angle = np.arctan2(sun_v_central_coord[:,0],
                                         sun_v_central_coord[:,2])
            
        return true_tracking_angle
        
    def get_backT_corr_angle(self, true_angle):
        
        one = np.ones((len(true_angle)))
        soil_angle = self.soil_angle*one
        value = np.abs((np.cos(true_angle-self.soil_angle))/
                                   (self.GCR_x*np.cos(self.soil_angle)))  
           
        backT_corr_angle = np.zeros((len(true_angle)))
        backT_corr_angle[value>=1] = 0
        backT_corr_angle[value<1] = -np.sign(true_angle[value<1])*np.arccos(
                                                         (np.abs(np.cos(true_angle[value<1]-soil_angle[value<1])))/
                                                         (self.GCR_x*np.cos(soil_angle[value<1])))
            
        return backT_corr_angle
    
    def get_corrected_tracking_angle(self, true_T_angle, backT_corr_angle):
            
        corrected_tiltY = true_T_angle + backT_corr_angle
                    
        return corrected_tiltY
    
    def get_limitated_angle(self, tiltY):
       
        ind = np.where(tiltY>np.pi/2)
        tiltY[ind] = 0
        ind = np.where(tiltY<-np.pi/2)
        tiltY[ind] = 0
               
        return tiltY

    def get_shading_factor_front(self, sun_vect_cc, tiltY):
        
        tiltY = tiltY*np.pi/180            
        teta_r_front = np.arctan2(sun_vect_cc[:,2],sun_vect_cc[:,0])           # np.arctan2(y, x) manages angles in the correct quadrants 
        teta_r_front[sun_vect_cc[:,2]<0] = np.nan                              # conversion degree-radian
        
        N = tiltY.shape[0]
        SF_front = np.empty(N, dtype=float)

        pos = tiltY>0 
        neg = tiltY<0 
        zero = tiltY == 0
        
        if np.any(pos) :                                                        # Isolating the moments when the tiltY is positive to compute the shading factor in the correct way
            
            ty=tiltY[pos]      
            tr=teta_r_front[pos]            
            one = np.ones((len(ty)))
                                           
            delta_H_h_l = (np.sin(ty)*self.block_dim_x)                         # Height difference between highest point of one panel and the lowest point of the panel just next to it
            delta_L_h_l_front = (self.block_space_x*one)-(np.cos(ty)*
                                                          self.block_dim_x)     # Distance between the highest point of one panel and the lowest point of the panel just next to it

            sun_elev_treshold_front = np.arctan2(delta_H_h_l,delta_L_h_l_front) # Sun elevation at which shade factor reaches 0, which is the min value of shade factor
                
            shade_factor_max = 0                                                # y_max for linear equation
            shade_factor_min = one                                              # y_min for linear equation
            
            theta_max = sun_elev_treshold_front                                 # x_max for linear equation
            theta_min = 0                                                       # x_min for linear equation
                                
            sf = self._linear_equation(shade_factor_max,shade_factor_min,theta_max,theta_min,tr)  #get the linear equation to find sf value
           
            
            sf = np.where(tr > sun_elev_treshold_front, 0.0, sf)                # When sun elevation is > treshold, then the SF is nul
            sf = np.where(tr > (np.pi - ty), 1.0, sf)                           # When sun elevation is > pi-tilt, then the SF is one
            
            SF_front[pos] = sf
            
        if np.any(neg) :                                                         # Isolating the moments when the tiltY is negative to compute the shading factor in the correct way
            
            ty=tiltY[neg]      
            tr=teta_r_front[neg]            
            one = np.ones((len(ty)))
            
            delta_H_h_l = (np.sin(-ty)*self.block_dim_x)                         # Height difference between highest point of one panel and the lowest point of the panel just next to it
            delta_L_h_l_front = (self.block_space_x*one)-(np.cos(ty)*
                                                          self.block_dim_x)      # Distance between the highest point of one panel and the lowest point of the panel just next to it

            sun_elev_treshold_front = np.arctan2(delta_H_h_l,-delta_L_h_l_front) # Sun elevation at which shade factor reaches 0, which is the min value of shade factor
                
            shade_factor_max = one
            shade_factor_min = 0                                                # Max value of the shade factor
               
            theta_max = np.pi
            theta_min = sun_elev_treshold_front
            
            sf = self._linear_equation(shade_factor_max,shade_factor_min,theta_max,theta_min,tr)
            
            sf = np.where(tr < sun_elev_treshold_front, 0.0, sf)
            sf = np.where(tr < (-ty), 1.0, sf)        
            
            SF_front[neg] = sf
            
        if np.any(zero):

            SF_front[zero] = np.where(sun_vect_cc[zero, 2] > 0.0, 0.0, 1.0)
                
        return SF_front
    
    def get_shading_factor_rear(self, sun_vect_cc, tiltY):
        
        tiltY = tiltY*np.pi/180            
        teta_r_rear = np.arctan2(sun_vect_cc[:,2],sun_vect_cc[:,0])           # np.arctan2(y, x) gère les angles dans les bons gradiants 
        teta_r_rear[sun_vect_cc[:,2]<0] = np.nan                                           # conversion degré-radian
        
        N = tiltY.shape[0]
        SF_rear = np.empty(N, dtype=float)
        
        pos= tiltY>0 
        neg = tiltY<0 
        zero = tiltY == 0
        
        if np.any(pos) :
            
            ty=tiltY[pos]      
            tr=teta_r_rear[pos]            
            one = np.ones((len(ty)))
                                           
            delta_H_h_l = (np.sin(ty)*self.block_dim_x)                         # Height difference between highest point of one panel and the lowest point of the panel just next to it
            delta_L_h_l_rear = (self.block_space_x*one)+(np.cos(ty)*
                                                          self.block_dim_x)        # Distance between the highest point of one panel and the lowest point of the panel just next to it
        
            sun_elev_treshold_rear = np.arctan2(delta_H_h_l,-delta_L_h_l_rear)     # Sun elevation at which shade factor reaches 0, which is the min value of shade factor
                
            shade_factor_max = one                                                 # Max value of the shade factor
            shade_factor_min = 0   
            
            theta_max = np.pi
            theta_min = sun_elev_treshold_rear
                                
            sf = self._linear_equation(shade_factor_max,shade_factor_min,theta_max,theta_min,tr)
           
            
            sf = np.where(tr < sun_elev_treshold_rear, 0.0, sf)                # When sun elevation is > treshold, then the SF is nul
            sf = np.where(tr < (np.pi - ty), 1.0, sf) 
            
            SF_rear[pos] = sf
            
        if np.any(neg) :
            
            ty=tiltY[neg]      
            tr=teta_r_rear[neg]            
            one = np.ones((len(ty)))
            
            delta_H_h_l = (np.sin(-ty)*self.block_dim_x)                      # Height difference between highest point of one panel and the lowest point of the panel just next to it
            delta_L_h_l_rear = (self.block_space_x*one)+(np.cos(ty)*
                                                          self.block_dim_x)   # Distance between the highest point of one panel and the lowest point of the panel just next to it
        
            sun_elev_treshold_rear = np.arctan2(delta_H_h_l,delta_L_h_l_rear) # Sun elevation at which shade factor reaches 0, which is the min value of shade factor
                
            shade_factor_max = 0
            shade_factor_min = one                                            
               
            theta_max = np.pi
            theta_min = sun_elev_treshold_rear
            
            sf = self._linear_equation(shade_factor_max,shade_factor_min,theta_max,theta_min,tr)
            
            sf = np.where(tr > sun_elev_treshold_rear, 0.0, sf)
            sf = np.where(tr > (-ty), 1.0, sf)        
            
            SF_rear[neg] = sf
            
        if np.any(zero):
        
            SF_rear[zero] = 1
         
        return SF_rear
        
    def _linear_equation (self,y_max,y_min,x_max,x_min,theta):
      
        m=(y_max-y_min)/(x_max-x_min)                                          # Determine m from the equation y = m*x + p
        p = np.where(m<0,1,-m*x_min)                                           # Determine p according to m profile where y=0 -> p=-m*x
        
        SF = theta*m+p
        SF[np.isnan(theta)] = 1.0                                              # SF = 1 when sun elevation is under horizon 
        
        return SF

    def get_n_years_albedo_from_csvfile(self, file: str, starting_year: str,
                                        ending_year: str, freq: str,
                                        albedo_default_value:float):

        albedo = pd.read_csv(
            os.path.join('INPUTS', 'CROPS', file + '.csv'),
            delimiter=',|;',
            engine='python')

        albedo.index = pd.to_datetime(albedo.date, dayfirst=True)

        # Modify datetime index to adapt to sun_vect and to light.

        new_index = pd.date_range("01-01-" + str(starting_year)
                                  + " 00:00:00",
                                  "31-12-" + str(ending_year)
                                  + " 23:59:59",
                                  freq=freq)

        albedo = albedo[['Albedo']].reindex(new_index).ffill()

        if albedo.dropna().empty:
            logger.warning("Please check that albedo file dates are consistent "
                           "with input Starting Year and Ending Year. "
                           "Default value used")
            albedo = albedo.fillna(albedo_default_value)
        nyears_albedo = {}

        for year in range(int(starting_year), int(ending_year)+1):
            one_year_df = albedo[albedo.index.year == year]
            nyears_albedo[str(year)] = one_year_df
        return nyears_albedo
