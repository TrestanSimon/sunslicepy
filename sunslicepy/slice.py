from abc import ABC, abstractmethod
import matplotlib.pyplot as plt
import numpy as np
import astropy.units as u
from astropy.coordinates import SkyCoord
from scipy.ndimage import convolve
from matplotlib import colors
from sunpy.coordinates import Helioprojective
from sunpy.map import MapSequence
from tqdm import tqdm
from sunslicepy.rasterize import bresenham_line, dda_line


class GenericSlice(ABC):
    def __init__(
            self,
            sequence_input: MapSequence,
            skycoords_input: SkyCoord,
    ):
        if not isinstance(sequence_input, MapSequence):
            raise Exception("sequence_input must be of type MapSequence.")
        if not isinstance(skycoords_input, SkyCoord):
            raise Exception("skycoords_input must be of type SkyCoords.")

        self.map_sequence = sequence_input
        self.skycoords_input = skycoords_input

        self.map_sequence.all_maps_same_shape()
        self.frame_n = len(self.map_sequence)
        self.time = [smap.date.datetime for smap in self.map_sequence]
        self.cmap = self.map_sequence[0].cmap

        # Necessary when points are not on disk
        with Helioprojective.assume_spherical_screen(
                center=self.observer(),
                only_off_disk=True
        ):
            self.curve_px, self.intensity = self._get_slice()
            self.curve_len = len(self.curve_px[0])
            self.curve_ds = self._get_curve_ds()

    @property
    @abstractmethod
    def observer(self):
        """"""

    @abstractmethod
    def _get_slice(self) -> (np.ndarray, np.ndarray):
        """
        :return:
            curve_px
            intensity
        """

    @abstractmethod
    def _get_curve_ds(self) -> np.ndarray:
        """"""

    def pixel_boxcar(self, x=3, t=None) -> (np.ndarray, np.ndarray, np.ndarray):
        if x is not None and t is None:
            if x <= 1 or x % 2 != 1:
                raise Exception("Keyword argument 'x' must be an odd integer greater than 1.")
            dx = int((x - 1) / 2.)
            smooth_curve_ds = self.curve_ds[dx:-dx]
            smooth_data = np.empty((self.frame_n, self.curve_len - x + 1))
            for f in range(self.frame_n):
                smooth_data[f] = np.convolve(self.intensity[f], np.ones(x) / float(x), 'valid')
            return smooth_data, self.time, smooth_curve_ds
        elif x is None and t is not None:
            raise NotImplemented
        elif x is not None and t is not None:
            if x <= 1 or x % 2 != 1:
                raise Exception("Keyword argument 'x' must be an odd integer greater than 1.")
            # dx = int((x - 1) / 2.)
            # dt = int((t - 1) / 2.)
            smooth_curve_ds = self.curve_ds  # [dx:-dx]
            smooth_time = self.time  # [dt:-dt]
            smooth_data = convolve(self.intensity, np.ones((t, x)) / float(t * x))  # [dt:-dt][dx:-dx]
            return smooth_data, smooth_time, smooth_curve_ds
        else:
            raise NotImplemented

    def peek(self, norm='log'):
        plt.pcolormesh(
            self.time,
            [ds.value for ds in self.curve_ds],
            self.intensity.T,
            cmap=self.cmap, norm=norm
        )
        plt.show()

    def peek_running_difference(self, norm=colors.Normalize(vmin=-200, vmax=200)):
        plt.pcolormesh(
            self.time[1:],
            [ds.value for ds in self.curve_ds],
            running_difference(self.intensity).T,
            cmap=self.cmap, norm=norm
        )
        plt.show()


class PointsSlice(GenericSlice):
    def observer(self):
        return self.skycoords_input[0].observer

    def _get_slice(self):
        intensity_cube = np.array([map_s.data for map_s in self.map_sequence])
        curve_len = len(self.skycoords_input)
        curve_px = np.empty((self.frame_n, curve_len, 2), dtype=int)
        intensity = np.empty((self.frame_n, curve_len), dtype=float)

        for f in tqdm(range(self.frame_n), unit='frames'):
            xf, yf = self.map_sequence[f].world_to_pixel(self.skycoords_input)
            xf, yf = np.round(xf), np.round(yf)
            for i in range(curve_len):
                xi, yi = int(xf[i].value), int(yf[i].value)
                curve_px[f][i] = yi, xi
                intensity[f][i] = intensity_cube[f][yi][xi]

        return curve_px, intensity

    def _get_curve_ds(self):
        curve_ds = np.empty(self.curve_len, dtype=u.Quantity)
        curve_ds[0] = 0 * u.arcsec
        for i in range(self.curve_len - 1):
            curve_ds[i + 1] = curve_ds[i] + self.skycoords_input[i + 1].separation(self.skycoords_input[i])
        return curve_ds


class BresenhamSlice(GenericSlice):
    """Slice using Bresenham's line algorithm"""
    def observer(self):
        return self.skycoords_input[0].observer

    def _get_slice(self):
        intensity_cube = np.array([map_s.data for map_s in self.map_sequence])
        intensity = None
        curve_px = None
        coords_n = len(self.skycoords_input)  # 2

        for f in tqdm(range(self.frame_n), unit='frames'):
            xp, yp = self.map_sequence[f].world_to_pixel(self.skycoords_input)
            coords = np.abs(np.array([
                [int(xi.value) for xi in xp],
                [int(yi.value) for yi in yp]]).T)
            for i in range(coords_n):
                coords[i] = int(np.round(coords[i][0])), int(np.round(coords[i][1]))

            x0, y0 = coords[0]
            x1, y1 = coords[1]
            curve_px_i = bresenham_line(x0, y0, x1, y1)
            curve_len = len(curve_px_i)
            if curve_px is None:
                curve_px = np.empty((self.frame_n, curve_len, 2), dtype=int)
            if intensity is None:
                intensity = np.empty((self.frame_n, curve_len), dtype=float)

            for i in range(curve_len):
                curve_px[f][i] = curve_px_i[i][1], curve_px_i[i][0]
                intensity[f][i] = intensity_cube[f][curve_px_i[i][1]][curve_px_i[i][0]]
        return curve_px, intensity

    def _get_curve_ds(self):
        curve_ds = np.empty(self.curve_len, dtype=u.Quantity)
        curve_ds[0] = 0 * u.arcsec

        intensity_coords = self.map_sequence[0].pixel_to_world(*(self.curve_px[0].T * u.pix))
        for i in range(self.curve_len - 1):
            curve_ds[i + 1] = curve_ds[i] + intensity_coords[i + 1].separation(intensity_coords[i])
        return curve_ds


class DDASlice(GenericSlice):
    """Slice using DDA line algorithm"""
    def observer(self):
        return self.skycoords_input[0].observer

    def _get_slice(self):
        intensity_cube = np.array([map_s.data for map_s in self.map_sequence])
        intensity = None
        curve_px = None
        coords_n = len(self.skycoords_input)  # 2

        for f in tqdm(range(self.frame_n), unit='frames'):
            xp, yp = self.map_sequence[f].world_to_pixel(self.skycoords_input)
            coords = np.abs(np.array([
                [int(xi.value) for xi in xp],
                [int(yi.value) for yi in yp]]).T)
            for i in range(coords_n):
                coords[i] = int(np.round(coords[i][0])), int(np.round(coords[i][1]))

            x0, y0 = coords[0]
            x1, y1 = coords[1]
            curve_px_i = dda_line(x0, y0, x1, y1)
            curve_len = len(curve_px_i)

            if curve_px is None:
                curve_px = np.empty((self.frame_n, curve_len, 2), dtype=int)
            if intensity is None:
                intensity = np.empty((self.frame_n, curve_len), dtype=float)

            for i in range(curve_len):
                curve_px[f][i] = curve_px_i[i][1], curve_px_i[i][0]
                intensity[f][i] = intensity_cube[f][curve_px_i[i][1]][curve_px_i[i][0]]
        return curve_px, intensity

    def _get_curve_ds(self):
        curve_ds = np.empty(self.curve_len, dtype=u.Quantity)
        curve_ds[0] = 0 * u.arcsec

        intensity_coords = self.map_sequence[0].pixel_to_world(*(self.curve_px[0].T * u.pix))
        for i in range(self.curve_len-1):
            curve_ds[i+1] = curve_ds[i] + intensity_coords[i+1].separation(intensity_coords[i])
        return curve_ds


class CustomSlice(GenericSlice):
    def __init__(self, sequence_input, skycoords_input, func: object):
        self.func = func
        super().__init__(sequence_input, skycoords_input)

    def observer(self):
        return self.skycoords_input[0].observer

    def _get_slice(self) -> (np.ndarray, np.ndarray):
        raise NotImplemented

    def _get_curve_ds(self) -> np.ndarray[u.Quantity]:
        raise NotImplemented


def running_difference(time_space_arr: np.ndarray) -> np.ndarray:
    t_len, x_len = time_space_arr.shape
    difference = np.empty((t_len-1, x_len), dtype=float)
    for t in range(t_len-1):
        for i in range(x_len):
            difference[t][i] = time_space_arr[t+1][i] - time_space_arr[t][i]
    return difference
