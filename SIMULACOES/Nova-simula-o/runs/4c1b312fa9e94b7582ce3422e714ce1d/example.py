#!/usr/bin/env python3
# -*- coding: utf-8 -*-

#Copyright (c) 2020-2024 - University of Liège - Digital Energy and Agriculture Lab (DEAL)
#Author : Roxane Bruhwyler (roxane.bruhwyler@uliege.be or roxane.bruhwyler@hotmail.com)
#This file is part of the PASE software, and is distributed under the MIT license.

import os
from pathlib import Path

from pase.user_support_tools import PASE_Logger
from pase.DATA_MANAGEMENT.yaml_inputs_provider import (YAML_Inputs_provider,
                                                       Inputs_aggregator)
from pase.DATA_MANAGEMENT.input_checker import InputsEvaluator
from pase.DATA_MANAGEMENT.OUTPUT.outputs_manager import OutputsManager
from pase.DATA_MANAGEMENT.weather_data_provider import (fetch_weather_from_pvgis,
                                                        get_cache_key,
                                                        Weather_data)
from pase.PHOTOVOLTAICS.configuration import PV_Configuration_3D
from pase.ENVIRONMENT.light import Sun_positions_sampled, Sun_positions, Light
from pase.ENVIRONMENT.light import Ray_casting_scene
from pase.ENVIRONMENT.mesh import Mesh
from pase.ENVIRONMENT.sky_model import ReinhartSky
from pase.PHOTOVOLTAICS.production import PV_Production
from pase.CROPS.run_crop_simulations import (run_crop_simu,
                                             visualize_map_of_a_variable)

PASE_Logger()

###############
# Load inputs #
###############
Loc_1 = YAML_Inputs_provider(file='Example1_loc.yaml', subpath='SCENARIOS').inputs
# Import PV system and PV modules parameters
AV_1 = YAML_Inputs_provider(file='Example1_AV.yaml', subpath='AV_CENTRAL').inputs
PV_module_1 = YAML_Inputs_provider(file='Example1_PV_Module.yaml', subpath=os.path.join('HARDWARE','PV_MODULES')).inputs
Structure = YAML_Inputs_provider(file='agrivoltaic_fence.yaml', subpath=os.path.join('HARDWARE', 'STRUCTURES')).inputs
crop_config = YAML_Inputs_provider(file='simple_example.yml', subpath=os.path.join('CROPS', 'config')).inputs

# InputsEvaluator is there to safeguard computing time and memory usage by checking some parameters values
input_checker = InputsEvaluator(Loc_1, AV_1)

PV_params_dict = Inputs_aggregator([AV_1, PV_module_1, Structure]).aggregated_inputs

om = OutputsManager(Loc_1['LocationName'],
                    Loc_1['SimulationStartingYear'],
                    Loc_1['SimulationEndingYear'])

variant_dir = om.setup_variant(loc=Loc_1,
                               av=AV_1,
                               pv_module=PV_module_1,
                               structure=Structure,
                               crop_config=crop_config,
                               source=__file__)

cache_key = get_cache_key(Loc_1['Latitude'],
                          Loc_1['Longitude'],
                          Loc_1['SimulationStartingYear'],
                          Loc_1['SimulationEndingYear'])

##################
# Pre-processing #
##################
# Import weather data and compute daily weather data
lat = Loc_1['Latitude']
lon = Loc_1['Longitude']
start_year = Loc_1['SimulationStartingYear']
end_year = Loc_1['SimulationEndingYear']

if Loc_1['WeatherDataOption'] == 1:
    raw_weather = om.load_or_fetch_weather(
        key=cache_key,
        fetch_fn=lambda: fetch_weather_from_pvgis(lat, lon, start_year, end_year)
    )
else:  # Weather data from csv file
    raw_weather=None

WD = Weather_data(Loc_1['Latitude'],
                  Loc_1['Longitude'],
                  Loc_1['SimulationStartingYear'],
                  Loc_1['SimulationEndingYear'],
                  Loc_1['WeatherDataOption'],
                  raw_weather,
                  Loc_1['WeatherFileName'],
                  Loc_1['DailyWeatherFileName']
                  )

print('PASE Studio: calculando posições solares', flush=True)
# Import sun positions

# sampled for the direct light model
Sun_positions_samp = Sun_positions_sampled(Loc_1['Latitude'],
                                      Loc_1['Longitude'],
                                      Loc_1['PrecisionLevelOnSunPosition'],
                                      Loc_1['LocationName'],
                                      len(WD.nyears_data[str(Loc_1['SimulationStartingYear'])]),
                                      Loc_1['TimeZone'])
# complete for the HDKR model
Sun_positions_complete = Sun_positions(Loc_1['Latitude'],
                                       Loc_1['Longitude'],
                                       len(WD.nyears_data[str(Loc_1['SimulationStartingYear'])]),
                                       Loc_1['TimeZone'])

print('PASE Studio: construindo geometria dos painéis e suportes', flush=True)
# Instantiation of the 3D PV central
PV_1_3Dconfig = PV_Configuration_3D(PV_params_dict,
                                    Sun_positions_samp.solar_vector,
                                    visualization=False)  # !!!! Problem with rotation angle that are negative

print('PASE Studio: preparando área de cultivo', flush=True)
# Initiation of the object containing points of interest to compute light
M = Mesh()

# Interest Zone Orientation Mode
M.set_interest_zone_orientation(Loc_1, AV_1)

# Add of the points of interests on the ground for crop models
M.add_oriented_plane_ground_mesh(
    Loc_1['Xmin_InterestZone'],
    Loc_1['Xmax_InterestZone'],
    Loc_1['Ymin_InterestZone'],
    Loc_1['Ymax_InterestZone'],
    Loc_1['dX_InterestZone'],
    Loc_1['dY_InterestZone'],
    flag="crop"
)

# Discrete sky model
discrete_sky = ReinhartSky(MF=Loc_1['MF']).reinhart_patches

print('PASE Studio: preparando clima e radiação', flush=True)
# Computation of sun and light data
Light_instance = Light(WD.nyears_data, Sun_positions_complete, Loc_1['DiffuseSkyType'])

# Instantiation of light ray casting model (direct and diffuse) with points of interest and scene
L = Ray_casting_scene(mesh=M,
                      geometry=PV_1_3Dconfig.PV_central_PD,
                      discrete_sky=discrete_sky)

print('PASE Studio: calculando sombras e irradiância', flush=True)
# Run light ray casting model (direct and diffuse) with points of interest and scene
L.get_light_maps(Sun_positions_samp.solar_vector,
                 visualization=False,
                 Sun_P_map_to_visualize=3)

# Integration of irradiation along days
L.get_daily_irradiation_map(Sun_positions_samp.SP, Light_instance.data,
                            visualization=False,
                            year=2005, julian_day=5)

# Studio visualization: direct map
# Studio visualization: diffuse map
# Studio visualization: daily map


##############
# Processing #
##############

print('PASE Studio: calculando produção fotovoltaica', flush=True)
# PV production model
PV_central = PV_Production(PV_params_dict, electrical_model='simple', module_parameters={})
PV_central.get_several_years_of_electricity_production(Sun_positions_complete, Light_instance.data, WD.nyears_data)

for _ in range(Loc_1['SimulationStartingYear'], Loc_1['SimulationEndingYear']+1):
    PV_prod = PV_central.production[str(_)]
    print(f'PV production for year {_}: {PV_prod["P_central"].sum():.2f} MW·h')

print('PASE Studio: calculando produção agrícola', flush=True)
# Crop model
#Temporary line, this parameter (option_2D) should be in SCENARIOS input files (general parameters)
option_2D = int(crop_config.get('Option2D', 0)) # Studio crop spatialization

agro_results = run_crop_simu(crop_config, option_2D,
                             WD.nyears_daily_data,
                             L.daily_irr_spat,
                             Loc_1)

print('PASE Studio: produção agrícola concluída', flush=True)
# Results are presented in PASE Studio.
