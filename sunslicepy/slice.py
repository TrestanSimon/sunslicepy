from abc import ABC, abstractmethod
from astropy.coordinates import SkyCoord
import astropy.units as u
import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import sunpy
import sunslicepy.processing
import tqdm
import warnings


class GenericSlice(ABC):
    """Base class for slices"""

    def __init__(
            self,
            sequence_input: sunpy.map.MapSequence,
            skycoords_input: SkyCoord,
    ):
        if not isinstance(sequence_input, sunpy.map.MapSequence):
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
        with sunpy.coordinates.screens.SphericalScreen(
                center=self.observer(),
                only_off_disk=True):
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

    def peek(self, norm='log'):
        plt.pcolormesh(
            self.time,
            [ds.value for ds in self.curve_ds],
            self.intensity.T,
            cmap=self.cmap, norm=norm
        )
        plt.show()

    def peek_running_difference(self, norm=mpl.colors.Normalize(vmin=-200, vmax=200)):
        plt.pcolormesh(
            self.time[1:],
            [ds.value for ds in self.curve_ds],
            sunslicepy.processing.running_difference(self.intensity).T,
            cmap=self.cmap, norm=norm
        )
        plt.show()


class PointsSlice(GenericSlice):
    """Slice at provided points"""

    def observer(self):
        return self.skycoords_input[0].observer

    def _get_slice(self):
        intensity_cube = np.array([map_s.data for map_s in self.map_sequence])
        curve_len = len(self.skycoords_input)
        curve_px = np.empty((self.frame_n, curve_len, 2), dtype=int)
        intensity = np.empty((self.frame_n, curve_len), dtype=float)

        for f in tqdm.tqdm(range(self.frame_n), unit='frames'):
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


class BetweenPointsSlice(GenericSlice):

    def __init__(
            self,
            sequence_input: sunpy.map.MapSequence,
            skycoords_input: SkyCoord,
            func,
            **kwargs,
    ):
        self._func = func
        self._func_kwargs = kwargs
        super().__init__(sequence_input, skycoords_input)

    def observer(self):
        return self.skycoords_input[0].observer

    def _get_slice(self):
        intensity_cube = np.array([map_s.data for map_s in self.map_sequence])
        intensity = None
        curve_px = None
        coords_n = len(self.skycoords_input)

        for f in tqdm.tqdm(range(self.frame_n), unit='frames'):
            xp, yp = self.map_sequence[f].world_to_pixel(self.skycoords_input)
            coords = np.abs(np.array([
                [int(np.round(xi.value)) for xi in xp],
                [int(np.round(yi.value)) for yi in yp]]).T)
            for i in range(coords_n):
                coords[i] = coords[i][0], coords[i][1]

            curve_px_i = None
            for i in range(coords_n-1):
                x0, y0 = coords[i]
                x1, y1 = coords[i+1]
                func_return = self._func(x0, y0, x1, y1, **self._func_kwargs)
                if curve_px_i is None:
                    curve_px_i = func_return
                else:
                    curve_px_i = np.append(curve_px_i, func_return, axis=0)
            curve_len = len(curve_px_i)

            if curve_px is None:
                curve_px = np.empty((self.frame_n, curve_len, 2), dtype=int)
            if intensity is None:
                intensity = np.empty((self.frame_n, curve_len), dtype=float)

            for i in range(curve_len):
                try:
                    curve_px[f][i] = curve_px_i[i][1], curve_px_i[i][0]
                    intensity[f][i] = intensity_cube[f][curve_px_i[i][1]][curve_px_i[i][0]]
                except IndexError:
                    warnings.warn("The number of pixels between the specified SkyCoords is not constant."
                                  "Pixels in excess of the original number will not be stored.")
        return curve_px, intensity

    def _get_curve_ds(self):
        curve_ds = np.empty(self.curve_len, dtype=u.Quantity)
        curve_ds[0] = 0 * u.arcsec

        intensity_coords = self.map_sequence[0].pixel_to_world(*(self.curve_px[0].T * u.pix))
        for i in range(self.curve_len - 1):
            curve_ds[i + 1] = curve_ds[i] + intensity_coords[i + 1].separation(intensity_coords[i])
        return curve_ds
