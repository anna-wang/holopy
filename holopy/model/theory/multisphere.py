# Copyright 2011, Vinothan N. Manoharan, Thomas G. Dimiduk, Rebecca
# W. Perry, Jerome Fung, and Ryan McGorty
#
# This file is part of Holopy.
#
# Holopy is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Holopy is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Holopy.  If not, see <http://www.gnu.org/licenses/>.
"""
Defines Multisphere theory class, which calculates scattering for multiple
spheres using the (exact) superposition method implemented in
modified version of Daniel Mackowski's SCSMFO1B.FOR.  Uses full radial
dependence of spherical Hankel functions for the scattered field.

.. moduleauthor:: Vinothan N. Manoharan <vnm@seas.harvard.edu>
.. moduleauthor:: Jerome Fung <fung@physics.harvard.edu>
"""

import numpy as np
import mie_f.mieangfuncs as mieangfuncs
import mie_f.scsmfo_min as scsmfo_min
import mie_f.miescatlib as miescatlib
from holopy.hologram import Hologram
from holopy import Optics
from holopy.utility.helpers import _ensure_array, _ensure_pair

from holopy.model.scatterer import Sphere, SphereCluster, Composite
from holopy.model.errors import TheoryNotCompatibleError
from holopy.model.theory.scatteringtheory import ScatteringTheory, ElectricField
from mie_f.mieangfuncs import singleholo
from mie_f.miescatlib import nstop, scatcoeffs

class Multisphere(ScatteringTheory):
    """
    Class that contains methods and parameters for calculating
    scattering using T-matrix superposition method.

    Attributes
    ----------
    imshape : float or tuple (optional)
        Size of grid to calculate scattered fields or
        intensities. This is the shape of the image that calc_field or
        calc_intensity will return
    phis : array 
        Specifies azimuthal scattering angles to calculate (incident
        direction is z)
    thetas : array 
        Specifies polar scattering angles to calculate
    optics : :class:`holopy.optics.Optics` object
        specifies optical train
    niter : integer (optional)
        maximum number of iterations to use in solving the interaction
        equations 
    meth : integer (optional)
        method to use to solve interaction equations.  Set to 0 for
        biconjugate gradient; 1 for order-of-scattering
    eps : float (optional)
        relative error tolerance in solution for interaction equations
    qeps1 : float (optional) 
        error tolerance used to determine at what order the
        single-sphere spherical harmonic expansion should be truncated
    qeps2 : float (optional) 
        error tolerance used to determine at what order the cluster
        spherical harmonic expansion should be truncated 

    Notes
    -----
    According to Mackowski's manual for SCSMFO1B.FOR [1]_ and later
    papers [2]_, the biconjugate gradient is generally the most
    efficient method for solving the interaction equations, especially
    for dense arrays of identical spheres.  Order-of-scattering may
    converge better for non-identical spheres.

    References
    ---------
    [1] Daniel W. Mackowski, SCSMFO.FOR: Calculation of the Scattering
    Properties for a Cluster of Spheres,
    ftp://ftp.eng.auburn.edu/pub/dmckwski/scatcodes/scsmfo.ps 

    [2] D.W. Mackowski, M.I. Mishchenko, A multiple sphere T-matrix
    Fortran code for use on parallel computer clusters, Journal of
    Quantitative Spectroscopy and Radiative Transfer, In Press,
    Corrected Proof, Available online 11 March 2011, ISSN 0022-4073,
    DOI: 10.1016/j.jqsrt.2011.02.019. 
    """

    def __init__(self, imshape=(256, 256), thetas=None, phis=None,
                 optics=None,  niter=200, eps=1e-6, meth=1, qeps1=1e-5, 
                 qeps2=1e-8): 

        # call base class constructor
        ScatteringTheory.__init__(self, imshape=imshape,
                                  thetas=thetas, phis=phis,
                                  optics=optics) 

        self.niter = niter
        self.eps = eps
        self.meth = meth
        self.qeps1 = qeps1
        self.qeps2 = qeps2

    def calc_field(self, scatterer):
        """
        Calculate fields for single or multiple spheres

        Parameters
        ----------
        scatterer : :mod:`holopy.model.scatterer` object
            scatterer or list of scatterers to compute field for

        Returns
        -------
        xfield, yfield, zfield : complex arrays with shape `imshape`
            x, y, z components of scattered fields

        Notes
        -----
        For multiple particles, this code superposes the fields
        calculated from each particle (using calc_mie_fields()). 
        """
        if isinstance(scatterer, Sphere):
            spheres = [scatterer]
        elif isinstance(scatterer, Composite):
            spheres = scatterer.get_component_list()
            # compatibility check: verify that the cluster only contains
            # spheres 
            if not scatterer.contains_only_spheres():
                for s in spheres:
                    if not isinstance(s, Sphere):
                        raise TheoryNotCompatibleError(self, s)
            # if it passes, superpose the fields
        else: raise TheoryNotCompatibleError(self, scatterer)

        xfield, yfield, zfield = calc_multisphere_fields(spheres)
        return xfield, yfield, zfield

    def calc_holo(self, scatterer, alpha=1.0):
        """
        Calculate hologram formed by interference between scattered
        fields and a reference wave
        
        Parameters
        ----------
        scatterer : :mod:`holopy.model.scatterer` object
            scatterer or list of scatterers to compute field for
        alpha : scaling value for intensity of reference wave

        Returns
        -------
        holo : :class:`holopy.hologram.Hologram` object
            Calculated hologram from the given distribution of spheres

        Notes
        -----
        For a single particle, this code uses a fast Fortran
        subroutine to calculate the hologram.  Otherwise it uses the
        Fortran subroutine for calculating the fields from each
        particle, then superposes them using numpy.
        """

        if isinstance(scatterer, Sphere):
            s = scatterer
            holo = forward_holo(self.imshape, self.optics,
                                s.n.real, s.n.imag, s.r, 
                                s.x, s.y, s.z, alpha)
        else:   # call base class calc_holo
            holo = ScatteringTheory.calc_holo(self, scatterer, 
                                              alpha=alpha)

        return Hologram(holo, optics = self.optics)

# TODO: Need to refactor fitting code so that it no longer relies on
# the legacy functions below.  Then remove.
par_ordering = ['n_particle_real_1',  'n_particle_real_2', 
                'n_particle_imag_1',  'n_particle_imag_2', 
                'radius_1', 'radius_2', 
                'x_com', 'y_com', 'z_com', 
                'scaling_alpha', 'euler_beta', 'euler_gamma',
                'gap_distance']

def _scaled_by_k(param_name):
    pars = ['radius_1', 'radius_2', 'x_com', 'y_com', 'z_com', 
            'gap_distance']
    return param_name in pars

def _scaled_by_med_index(param_name):
    pars = ['n_particle_real_1', 'n_particle_imag_1', 'n_particle_real_2',
            'n_particle_imag_2']
    return param_name in pars

def _forward_holo(size, opt, scat_dict): 
    '''
    Internal use; passes everything to public forward_holo non-dimensionally.
    '''
    # make sure these params have value of None if they do not exist.
    # The fitter will assume a value for them in that case, but it
    # will fail if they don't exist at all.
    scat_dict['n_particle_real_2'] = scat_dict.get('n_particle_real_2')
    scat_dict['n_particle_imag_2'] = scat_dict.get('n_particle_imag_2')
    
    return forward_holo(size, opt, dimensional = False, **scat_dict)

def calc_multisphere_fields(size, opt, n_particle_real, n_particle_imag,
                            radius, x, y, z, dimensional = True):
    '''
    Calculate the scattered electric field from a set of spherical
    particle using SCSMFO multisphere code.  This is a python wrapper
    around the fortran function mie_f.tmatrix_fields 

    Parameters
    ----------
    size : int or tuple
        Dimension of hologram.
    opt : instance of the :class:`holopy.optics.Optics` class
        Optics class containing information about the optics
        used in generating the hologram.
    n_particle_real : float
        Refractive index of particle.
    n_particle_imag : float
        Refractive index of particle.
    radius : float
        Radius of bead in microns.
    x : float
        x-position of particle in pixels.
    y : float
        y-position of particle in pixels.
    z : float
        z-position of particle in microns
    dimensional: bool
       If False, assume all lengths non-dimensionalized by k and all
       indices relative (divided by medium index).

    Returns
    -------
    Returns three arrays: the x-, y-, and z-component of scattered fields.

    Notes
    -----
    x- and y-coordinate of particle are given in pixels where
    (0,0) is at the top left corner of the image. 
    '''

    # Allow size and pixel size to be either 1 number (square) 
    #    or rectangular
    if np.isscalar(size):
        xdim, ydim = size, size
    else:
        xdim, ydim = size
    px, py = opt.pixel

    # Determine particle properties in scattering units
    if dimensional:
        m_p = (n_particle_real + 1.j * n_particle_imag) / opt.index
        x_p = opt.wavevec * radius        
        kcoords = opt.wavevec * np.array([x, y, z])
    else:
        m_p = (n_particle_real + 1.j * n_particle_imag)
        x_p = radius
        kcoords = np.array([x, y, z])

    # Calculate maximum order lmax of Mie series expansion.
    lmax = miescatlib.nstop(x_p)
    # Calculate scattering coefficients a_l and b_l
    albl = miescatlib.scatcoeffs(x_p, m_p, lmax)

    # mieangfuncs.f90 works with everything dimensionless.
    gridx = opt.wavevec * np.mgrid[0:xdim] * px # (0,0) at upper left convention
    gridy = opt.wavevec * np.mgrid[0:ydim] * py

    # need to calculate amn coefficients
    amn = scsmfo_min.amncalc()

    escat_x, escat_y, escat_z = mieangfuncs.tmatrix_fields(gridx,
                                                       gridy, kcoords,
                                                       albl,
                                                       opt.polarization)


    return escat_x, escat_y, escat_z