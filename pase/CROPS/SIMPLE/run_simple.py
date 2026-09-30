#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#Copyright (c) 2020-2024 - University of Liège - Digital Energy and Agriculture Lab (DEAL)
#Author : Roxane Bruhwyler (roxane.bruhwyler@uliege.be or roxane.bruhwyler@hotmail.com)
#This file is part of the PASE software, and is distributed under the MIT license.

import pandas as pd
import numpy as np
from pase.CROPS.SIMPLE import simple
from pase.CROPS.SIMPLE import water_balance
from pase.DATA_MANAGEMENT.yaml_inputs_provider import YAML_Inputs_provider
from pase.CROPS.evapotranspiration_FAO56_PM import get_ET0


def run_independant_years_of_crop(config, option_2D, WD, daily_irr, lat, alt):
    
    Crop_init = YAML_Inputs_provider(file = f"CROPS/SIMPLE/{config['CropInit']}").inputs
    list_rows_to_skip = list(np.arange(1, int(Crop_init['CropID']), 1))
    Crop_param = pd.read_csv(f"INPUTS/CROPS/SIMPLE/{config['CropParameters']}",
                             skiprows=lambda x: x in list_rows_to_skip, 
                             nrows=1).to_dict('records')[0]
    Soil_param = YAML_Inputs_provider(file = f"CROPS/SIMPLE/{config['SoilInit']}").inputs
    
    Soil_plot = water_balance.Soil(Soil_param)
    Crop_plot = simple.Crop(Crop_param, 
                            Crop_init)
    
    for year in WD.keys():  
        
        Crop_plot.initiate_one_year_data_dictionaries()
        Soil_plot.initiate_one_year_data_dictionaries()
        Crop_plot.init_crop(Crop_init)
        Soil_plot.init_soil()
               
        for day in WD[year].index:
            
            if day.day_of_year==366:
                break
            
            if option_2D==1:
                irradiation = daily_irr[year][:,day.day_of_year-1]
            else:
                # The integrated SIMPLE mode represents the crop zone by its
                # mean daily irradiation; keep the result scalar and avoid
                # calculating the full 2D map when the user chooses fast mode.
                irradiation = float(np.nanmean(daily_irr[year][:,day.day_of_year-1]))
            
            ET0 = get_ET0(WD[year]['Avg_temp'][day],
                          WD[year]['Min_temp'][day],
                          WD[year]['Max_temp'][day],
                          WD[year]['Avg_WS_2m'][day],
                          WD[year]['Vap_press'][day],
                          irradiation,
                          Crop_plot.LAI,
                          day.day_of_year,
                          len(WD[year]['Avg_temp']),
                          lat,
                          alt,
                          Soil_param['Albedo'])
            
            Soil_plot.hydric_balance(WD[year]['Rain'][day],
                                     ET0,
                                     0,
                                     day)
            
            if (day.day_of_year >= Crop_init['StartDayCropModel'] 
                and day.day_of_year <= Crop_init['EndDayCropModel']):
            
                Crop_plot.growth(WD[year]['Avg_temp'][day],
                                 WD[year]['Max_temp'][day],
                                 WD[year]['CO2'][day],
                                 irradiation,
                                 ET0,
                                 Soil_plot.dict_transpi[str(day)],
                                 day)
            
        Soil_plot.fill_nyears_data_dict(year)
        Crop_plot.fill_nyears_data_dict(year)
        

    return Soil_plot, Crop_plot
