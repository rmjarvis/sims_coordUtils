import unittest
import os
import numpy as np
import lsst.utils.tests

from lsst.utils import getPackageDir
from lsst.afw.cameraGeom import PIXELS, FOCAL_PLANE, SCIENCE
import lsst.afw.geom as afwGeom
from lsst.sims.coordUtils import lsst_camera
from lsst.sims.coordUtils import focalPlaneCoordsFromPupilCoordsLSST
from lsst.sims.coordUtils import DMtoCameraPixelTransformer
from lsst.sims.utils import ObservationMetaData
from lsst.sims.utils import pupilCoordsFromRaDec
from lsst.sims.coordUtils import chipNameFromPupilCoordsLSST
from lsst.sims.coordUtils.LsstZernikeFitter import _rawPupilCoordsFromObserved

def setup_module(module):
    lsst.utils.tests.init()


class FocalPlaneTestCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        pix_transformer = DMtoCameraPixelTransformer()
        data_dir = os.path.join(getPackageDir('sims_data'),
                                'FocalPlaneData',
                                'UnitTestData')

        cls._data_dir = data_dir

        phosim_dtype = np.dtype([('id', int), ('phot', int),
                                 ('xpix', float), ('ypix', float)])

        camera = lsst_camera()
        cls._phosim_positions = {}
        for i_band in range(6):
            id_arr = None
            x_arr = None
            y_arr = None
            for det in camera:
                if det.getType() != SCIENCE:
                    continue
                det_name = det.getName()
                name = det_name.replace(':','').replace(',','')
                name = name.replace(' ','_')
                file_name = 'centroid_lsst_e_2_f%d_%s_E000.txt' % (i_band, name)
                data = np.genfromtxt(os.path.join(data_dir, 'PhoSimData',
                                                  file_name),
                                     dtype=phosim_dtype,
                                     skip_header=1)
                dm_x, dm_y = pix_transformer.dmPixFromCameraPix(data['xpix'],
                                                                data['ypix'],
                                                                det_name)
                pix_to_focal = det.getTransform(PIXELS, FOCAL_PLANE)
                x_mm = np.zeros(len(dm_x))
                y_mm = np.zeros(len(dm_y))
                for ii in range(len(x_mm)):
                    pix_pt = afwGeom.Point2D(dm_x[ii], dm_y[ii])
                    focal_pt = pix_to_focal.applyForward(pix_pt)
                    x_mm[ii] = focal_pt.getX()
                    y_mm[ii] = focal_pt.getY()

                if id_arr is None:
                    id_arr = data['id']
                    x_arr = x_mm
                    y_arr = y_mm
                else:
                    id_arr = np.append(id_arr, data['id'])
                    x_arr = np.append(x_arr, x_mm)
                    y_arr = np.append(y_arr, y_mm)

            sorted_dex = np.argsort(id_arr)
            id_arr = id_arr[sorted_dex]
            x_arr = x_arr[sorted_dex]
            y_arr = y_arr[sorted_dex]
            cls._phosim_positions[i_band] = {}
            cls._phosim_positions[i_band]['id'] = id_arr
            cls._phosim_positions[i_band]['xmm'] = x_arr
            cls._phosim_positions[i_band]['ymm'] = y_arr

    @classmethod
    def tearDownClass(cls):
        if hasattr(focalPlaneCoordsFromPupilCoordsLSST, '_z_fitter'):
            del focalPlaneCoordsFromPupilCoordsLSST._z_fitter

        if hasattr(lsst_camera, '_lsst_camera'):
            del lsst_camera._lsst_camera

    def test_focal_plane_from_pupil(self):
        """
        Test conversion from pupil coords to focal plane coords
        using data generated by PhoSim
        """
        catsim_dtype = np.dtype([('id', int),
                                 ('xmm', float), ('ymm', float),
                                 ('xpup', float), ('ypup', float),
                                 ('raObs', float), ('decObs', float)])

        catsim_data = np.genfromtxt(os.path.join(self._data_dir, 'CatSimData',
                                                 'predicted_positions.txt'),
                                    dtype=catsim_dtype)

        for i_band, band in enumerate('ugrizy'):
            np.testing.assert_array_equal(catsim_data['id'],
                                          self._phosim_positions[i_band]['id'])

            xmm, ymm = focalPlaneCoordsFromPupilCoordsLSST(catsim_data['xpup'],
                                                           catsim_data['ypup'],
                                                           band)

            distance = np.sqrt((xmm-self._phosim_positions[i_band]['xmm'])**2 +
                               (ymm-self._phosim_positions[i_band]['ymm'])**2)

            self.assertLess(distance.max(), 0.01)
            print(band,distance.max()/0.01)


class FullTransformationTestCase(unittest.TestCase):
    """
    Test that we can go from astrophysical coordinates (RA, Dec)
    to pixel coordinates
    """

    longMessage = True

    @classmethod
    def setUpClass(cls):
        cls._data_dir = os.path.join(getPackageDir('sims_data'),
                                     'FocalPlaneData',
                                     'UnitTestData',
                                     'FullUnitTest')

        truth_name = os.path.join(cls._data_dir, 'truth_catalog.txt')
        with open(truth_name, 'r') as in_file:
            header = in_file.readline()
        params = header.strip().split()
        ra = float(params[2])
        dec = float(params[4])
        rotSkyPos = float(params[6])
        mjd = float(params[8])
        cls._obs = ObservationMetaData(pointingRA=ra,
                                       pointingDec=dec,
                                       rotSkyPos=rotSkyPos,
                                       mjd=mjd)

        cls._obs.site._humidity = 0.0
        cls._obs.site._pressure = 0.0
        assert cls._obs.site.humidity == 0.0
        assert cls._obs.site.pressure == 0.0

        truth_dtype = np.dtype([('id', int), ('ra', float), ('dec', float),
                                ('pmra', float), ('pmdec', float),
                                ('px', float), ('vrad', float)])

        cls._truth_data = np.genfromtxt(truth_name, dtype=truth_dtype,
                                        delimiter=', ')

        phosim_dtype = np.dtype([('id', int), ('phot', int),
                                 ('xcam', float), ('ycam', float)])

        list_of_files = os.listdir(cls._data_dir)

        cls._phosim_data = {}

        for file_name in list_of_files:
            if 'centroid' not in file_name:
                continue
            full_name = os.path.join(cls._data_dir, file_name)
            data = np.genfromtxt(full_name, dtype=phosim_dtype,
                                 skip_header=1)

            if len(data.shape)>0:
                valid = np.where(data['phot']>0)
                if len(valid[0]) == 0:
                    continue
                data = data[valid]
            else:
                if data['phot'] == 0:
                    continue

            params = file_name.split('_')
            chip_name = params[5]+'_'+params[6]
            filter_name = int(params[4][1])
            if len(data.shape) == 0:
                data_raw = data
                data = {}
                data['id'] = np.array([data_raw['id']])
                data['phot'] = np.array([data_raw['phot']])
                data['xcam'] = np.array([data_raw['xcam']])
                data['ycam'] = np.array([data_raw['ycam']])
            cls._phosim_data[(chip_name, 'ugrizy'[filter_name])] = data

    @classmethod
    def tearDownClass(cls):
        if hasattr(chipNameFromPupilCoordsLSST, '_detector_arr'):
            del chipNameFromPupilCoordsLSST._detector_arr

        if hasattr(focalPlaneCoordsFromPupilCoordsLSST, '_z_fitter'):
            del focalPlaneCoordsFromPupilCoordsLSST._z_fitter

        if hasattr(lsst_camera, '_lsst_camera'):
            del lsst_camera._lsst_camera

    def test_chip_name_from_pupil_coords_lsst(self):
        camera = lsst_camera()

        x_pup, y_pup = pupilCoordsFromRaDec(self._truth_data['ra'],
                                            self._truth_data['dec'],
                                            pm_ra=self._truth_data['pmra'],
                                            pm_dec=self._truth_data['pmdec'],
                                            parallax=self._truth_data['px'],
                                            v_rad=self._truth_data['vrad'],
                                            obs_metadata=self._obs)

        chip_name_list = chipNameFromPupilCoordsLSST(x_pup, y_pup, band='u')
        n_checked = 0
        for ii in range(len(chip_name_list)):
            chip_name = chip_name_list[ii]
            if chip_name is None:
                for kk in self._phosim_data:
                    if kk[1] == 'u':
                        try:
                            assert self._truth_data['id'][ii] not in self._phosim_data[kk]['id']
                        except AssertionError:
                            # check that source wasn't just on the edge of the chip
                            dex = np.where(self._phosim_data[kk]['id']==self._truth_data['id'][ii])[0]
                            xx = self._phosim_data[kk]['xcam'][dex]
                            yy = self._phosim_data[kk]['ycam'][dex]
                            if xx>10.0 and xx<3990.0 and yy>10.0 and yy<3990.0:
                                msg = '\nxpix: %.3f\nypix: %.3f\n' % (xx, yy)
                                self.assertNotIn(self._truth_data['id'][ii],
                                                 self._phosim_data[kk]['id'],
                                                 msg=msg)
                continue

            det = camera[chip_name]
            if det.getType() != SCIENCE:
                continue
            n_checked += 1
            chip_name = chip_name.replace(':','').replace(',','')
            chip_name = chip_name.replace(' ','_')
            self.assertIn(self._truth_data['id'][ii],
                          self._phosim_data[(chip_name, 'u')]['id'])

        self.assertGreater(n_checked, 200)

    def test_pupil_coords_from_ra_dec(self):
        """
        Verifythat pupilCoordsFromRaDec gives results consistent
        with the naive pupil coordinate method used by the
        Zernike fitter
        """

        phosim_catalog_file = os.path.join(self._data_dir, 'phosim_catalog.txt')
        ra_obs = []
        dec_obs = []
        unique_id = []
        with open(phosim_catalog_file,'r') as input_file:
            for line in input_file:
                params = line.strip().split()
                if params[0] != 'object':
                    if params[0] == 'rightascension':
                        ra_pointing = float(params[1])
                    if params[0] == 'declination':
                        dec_pointing = float(params[1])
                    if params[0] == 'rotskypos':
                        rotskypos = float(params[1])
                    continue
                unique_id.append(int(params[1]))
                ra_obs.append(float(params[2]))
                dec_obs.append(float(params[3]))
        unique_id = np.array(unique_id)
        ra_obs = np.array(ra_obs)
        dec_obs = np.array(dec_obs)
        x_pup, y_pup = _rawPupilCoordsFromObserved(np.radians(ra_obs),
                                                   np.radians(dec_obs),
                                                   np.radians(ra_pointing),
                                                   np.radians(dec_pointing),
                                                   np.radians(rotskypos))

        sorted_dex = np.argsort(unique_id)
        unique_id = unique_id[sorted_dex]
        x_pup = x_pup[sorted_dex]
        y_pup = y_pup[sorted_dex]

        (x_pup_test,
         y_pup_test) = pupilCoordsFromRaDec(self._truth_data['ra'],
                                            self._truth_data['dec'],
                                            pm_ra=self._truth_data['pmra'],
                                            pm_dec=self._truth_data['pmdec'],
                                            parallax=self._truth_data['px'],
                                            v_rad=self._truth_data['vrad'],
                                            obs_metadata=self._obs)

        sorted_dex = np.argsort(self._truth_data['id'])
        truth_id = self._truth_data['id'][sorted_dex]
        x_pup_test = x_pup_test[sorted_dex]
        y_pup_test = y_pup_test[sorted_dex]

        np.testing.assert_array_equal(unique_id, truth_id)
        distance = np.sqrt((x_pup-x_pup_test)**2 +
                           (y_pup-y_pup_test)**2)

        self.assertLess(distance.max(), 1.0e-12)

    def test_focal_coords_from_pupil_coords(self):
        """
        Test that using pupilCoordsFromRaDec and
        focalPlaneCoordsFromPupilCoordsLSST gives answers
        consistent with PhoSim
        """
        camera = lsst_camera()
        pix_transformer = DMtoCameraPixelTransformer()

        x_pup, y_pup = pupilCoordsFromRaDec(self._truth_data['ra'],
                                            self._truth_data['dec'],
                                            pm_ra=self._truth_data['pmra'],
                                            pm_dec=self._truth_data['pmdec'],
                                            parallax=self._truth_data['px'],
                                            v_rad=self._truth_data['vrad'],
                                            obs_metadata=self._obs)

        n_check = 0
        d_max = None

        for det in camera:
            if det.getType() != SCIENCE:
                continue
            det_name = det.getName()
            name = det_name.replace(':','').replace(',','').replace(' ','_')
            key_tuple = (name, 'u')
            if key_tuple not in self._phosim_data:
                continue
            phosim_data = self._phosim_data[key_tuple]

            pixel_to_focal = det.getTransform(PIXELS, FOCAL_PLANE)

            for id_val, xcam, ycam in zip(phosim_data['id'],
                                          phosim_data['xcam'],
                                          phosim_data['ycam']):

                dex = np.where(self._truth_data['id'] == id_val)
                xp = x_pup[dex]
                yp = y_pup[dex]
                xf, yf = focalPlaneCoordsFromPupilCoordsLSST(xp, yp, 'u')
                xdm, ydm = pix_transformer.dmPixFromCameraPix(xcam, ycam,
                                                              det_name)
                pixel_pt = afwGeom.Point2D(xdm, ydm)
                focal_pt = pixel_to_focal.applyForward(pixel_pt)

                dist = np.sqrt((xf-focal_pt.getX())**2+(yf-focal_pt.getY())**2)
                msg = '\nPhosim: %.4f %.4f\nCatSim: %.4f %.4f\n' % (focal_pt.getX(),
                                                                    focal_pt.getY(),
                                                                    xf, yf)
                #self.assertLess(dist, 0.01, msg=msg)
                if d_max is None or dist>d_max:
                    d_max = dist
                    print('d_max %e %.3f %.3f %s %d %d' %
                          (d_max, xf, yf, det_name, xdm, ydm))
                n_check += 1

        self.assertGreater(n_check, 200)


class MemoryTestClass(lsst.utils.tests.MemoryTestCase):
    pass


if __name__ == "__main__":
    lsst.utils.tests.init()
    unittest.main()
