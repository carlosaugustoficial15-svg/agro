#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Copyright (c) 2020-2024 - University of Liège - Digital Energy and Agriculture Lab (DEAL)
Author : Joran Dartevelle <Joran.Dartevelle@uliege.be>
This file is part of the PASE software, and is distributed under the MIT license.
"""

import numpy as np
from pase.conversion_functions import rotation_coordinate


class Diffuser:
    """
    General class for diffusers, allowing transfer functions not to be specified.
    Must be completed (by inheritance) by a specific diffuser class such as Lenticular_diffuser.

    Input
        azimuth: Diffuser azimuth in the global reference frame in degrees.
        elevation: Diffuser elevation in the global reference frame in degrees.

    Attributes:
        x_dr, y_dr, z_dr: Transmitted ray directions in the diffuser reference frame.
        azimuth_diff, elevation_diff: Diffuser azimuth and elevation in the global reference frame in radians
                                      using the trigonometric convention for defining angles.

    Diffuser reference frame (e_x, e_y, e_z):
        e_z: Normal of the diffuser plane
        e_y: unit vector in the direction of the diffuser lenses in the case of a lenticular diffuser
        e_x: Cross product between e_y and e_z
    """

    def __init__(self, azimuth, elevation):
        self.x_dr = None
        self.z_dr = None
        self.y_dr = None
        self.azimuth_diff = -np.radians(azimuth-90)
        self.elevation_diff = np.radians(elevation)

    def generate_direction_diffuser_referential(self, vect_sun, angle_discr):
        """
        Call the transfer function to compute the direction of the transferred light
        with a given resolution.
        Input:
            vect_sun: the sun vectors (nSunPos x 3)
            angle_discr: the angle resolution for the transmitted light direction in degrees

        Output:
            x_dr, y_dr, z_dr: Transmitted ray directions in the diffuser reference frame (nSunPos x Nvec x 3).
            rho: energy contained in each vector (nSunPos x Nvec)
            ds: Size of the segment belonging to the vector (nSunPos x Nvec)
        """
        self.x_dr, self.y_dr, self.z_dr, self.rho, self.ds = self.transfer_function(vect_sun, angle_discr)

    def get_direction_sky_referential(self):
        """
        Call the function to switch to the sky reference frame
        Output:
            x_sr, y_sr, z_sr: Transmitted ray directions in the global reference frame (nSunPos x Nvec x 3).
        """
        y_vector = np.array([0, 1, 0])
        z_vector = np.array([0, 0, 1])
        vect_0 = np.array([self.x_dr, self.y_dr, self.z_dr])
        vect_1 = rotation_coordinate(vect_0, z_vector, self.azimuth_diff)
        vect_2 = rotation_coordinate(vect_1, y_vector, self.elevation_diff)
        self.x_sr, self.y_sr, self.z_sr = vect_2

    def get_light_direction(self, vect_sun, discr, sigma, angle_discr):
        """
        General function to compute the directions and transform them in the right frame.
        Input:
            vect_sun: sun vectors (nSolPos x 3)
            discr: Direction of the sky patches (nPatch x 3)
            sigma: Size of the sky patch (nPatch)
            angle_discr: angular resolution of transmitted rays created.
        Output:
            W: Weight of the transmitted rays associate with each patch (nSolPos x nPatch)
        """
        self.generate_direction_diffuser_referential(vect_sun, angle_discr)
        self.get_direction_sky_referential()
        W = self.get_discretized_BSDF(discr, sigma)
        return W

    def get_discretized_BSDF(self, discr, sigma):
        """
        Compute the weight associate with each element of the discretization (discr).
        Input :
            discr (np.array of size (nPatch, 3):Direction of the sky patches, nPatch = number of patches
            sigma (np.array of size (nPatch): area proxy of each patch
            kernel (str): projection kernel.
        Output :
            W_trans (np.array of size (nSolPos, nPatch): Weight associate with each patch,
                                                        nSolPos = number of solar position
        """
        sigma2 = np.concatenate([np.sqrt(sigma),np.sqrt(sigma)])[np.newaxis, np.newaxis, :]
        discr2 = np.repeat(np.array([[1, 1, -1]]), discr.shape[0], axis=0) * discr
        sphere = np.concatenate([discr, discr2], axis=0)
        pts = np.stack([self.x_sr, self.y_sr, self.z_sr], axis=2)
        g = self.get_integration_kernel(pts, sphere, sigma2) #np.exp(-np.arccos(A) ** 2 / (2 * sigma2** 2))
        norm = g.sum(axis=2)[:, :, np.newaxis]
        g_norm = g/norm
        self.W = np.einsum('ij,ij,ijl->il',self.rho, self.ds, g_norm)    #sum_j self.rho_ij*self.ds_ij *g(A, sigma2)_ijl) --> il #i solar dimension and l sky dimension
        W_trans = self.W[:,:discr.shape[0]]
        return W_trans

    def get_transmission_reflection_weight(self):
        """
        Give the weight for the reflection and transmission hemisphere.
        Output:
            W_trans (np.array of size (nSolPos, nPatch): Weight associate with each patch for the transmission
            W_refl  (np.array of size (nSolPos, nPatch): Weight associate with each patch for the reflection
        """
        N = self.W.shape[1]
        W_trans = self.W[:N//2]
        W_refl = self.W[N//2:]
        return W_trans, W_refl

    def transfer_function(self, vect_sun, method, angle_discr):
        return NotImplementedError

    def get_integration_kernel(self, pts, sphere, sigma):
        """
        Compute the integration kernel for each patch
        Input
            pts (nPosSol, Nvec, 3): direction of the transmitted rays in the global reference frame
            sphere (2*nPatch, 3): direction of the sky patches
            sigma (newaxis, newaxis, 2*nPatch): size of each patch

        Output:
            g (nSolPos, Nvec, nPatch): radial gaussian integration kernel
        """
        dist = np.einsum('ijk, lk->ijl', pts, sphere)
        g = np.exp(-np.arccos(dist) ** 2 / (2 * sigma** 2))
        return g


class LenticularDiffuser(Diffuser):
    """
    Specific class that inherits from the Diffuser class. Adds the transfer function specific to lenticular diffusers

    Omega = Lens aperture angle in degrees
    """

    def __init__(self, azimuth_diff, elevation_diff, omega=30, res=0.1, **kwargs):
        super().__init__(azimuth_diff, elevation_diff)
        self.omega = np.deg2rad(omega)  # aperture angle
        l = np.array([[[0,1,0],[0,0,1]]]).T
        l = rotation_coordinate(l,np.array([0,0,1]), self.azimuth_diff)
        l = rotation_coordinate(l, np.array([0, 1, 0]), self.elevation_diff)
        self.len_vector = l[:, 0,0]
        self.normal =l[:,1,0]
        self.res=res

    def transfer_function(self, vect_sun, angle_discr):
        """
        Specific transfer function for lenticular diffusers.

        Gamma : Angle between the lens direction and the sun direction
        beta : Angle between the diffuser plane and the plane created by the
        sun vector and the lens vector.

        Input :
            vect_sun (nSunPos, 3): sun vectors
            angle_res (scalar): output angular resolution in radians

        Return :
            x, y, z: the direction of the transmitted light in the diffuser frame
        """
        res = self.res*angle_discr if self.res is not None else 0.1
        gamma = self.get_gamma_angle(vect_sun, res)
        beta= self.get_beta_angle(vect_sun, res)
        x = -np.sin(gamma) * (np.sin(beta-res/2) - np.sin(beta+res/2))/res
        y = np.cos(gamma)
        z = -np.sin(gamma) * (np.cos(beta+res/2) - np.cos(beta-res/2))/res
        ds = np.sin(gamma) * res
        L = ds.sum(axis=1)[:, np.newaxis]
        L = np.tile(L, (1, int((2 * self.omega + res) // res )))
        rho = 1/L
        return x, y, z, rho, ds

    def get_beta_angle(self, vect_sun, angle_res):
        """
        Compute the beta angle (i.e. angle between the diffuser plane and
        the plane defined by the sun vector and the lens vector.)

        Input:
            vect_sun (nSunPos, 3): sun vectors
            angle_res (scalar): output angular resolution in radians
        Output:
            beta (nSolPos, Nvec): beta angle in radians
        """
        plan_sunl = np.cross(vect_sun, self.len_vector)
        norm_psl = np.linalg.norm(plan_sunl, axis=1)
        ind = np.where(np.linalg.norm(plan_sunl, axis=1) == 0)
        norm_psl[ind] = 1
        cos_beta = np.clip(np.sum(plan_sunl * self.normal, axis=1) / (norm_psl * np.linalg.norm(self.normal)), -1, 1)
        beta = np.arccos(cos_beta)
        beta_t = np.arange(-self.omega+angle_res/2, self.omega + angle_res/2, angle_res)
        beta = beta[:, np.newaxis] + beta_t
        return beta

    def get_gamma_angle(self, vect_sun, angle_res):
        """
        Compute the gamma angle (i.e. the Angle between the lens direction
        and the sun direction)

        Input :
            vect_sun (nSunPos, 3): sun vectors
            angle_res (scalar): output angular resolution in radians
        Output:
            gamma (nSolPos, Nvec): gamma angle in radians, Nvec is the number of segments
            discretizing the diffuser trace
        """
        cos_gamma = np.clip(np.sum(vect_sun * self.len_vector, axis=1), -1, 1)
        gamma = np.arccos(cos_gamma)
        gamma = gamma[:, np.newaxis]
        gamma = np.tile(gamma, (1, int((2 * self.omega + angle_res) // angle_res)))
        return gamma
