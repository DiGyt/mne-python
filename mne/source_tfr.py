
# License: BSD (3-clause)

import copy
import numpy as np

from .filter import resample
from .utils import (_check_subject, verbose, _time_mask, _check_option,
                    _validate_type)
from .io.base import ToDataFrameMixin, TimeMixin
from .externals.h5io import write_hdf5


class SourceTFR(ToDataFrameMixin, TimeMixin):
    """Class for time-frequency transformed source level data.

    Parameters
    ----------
    data : array, shape (n_dipoles, n_freqs, n_times) | tuple, shape (2,)
        Time-frequency transformed data in source space. The data can either
        be a single array or a tuple with two arrays: "kernel" shape
        (n_vertices, n_sensors) and "sens_data" shape (n_sensors, n_freqs,
        n_times). In this case, the source space data corresponds to
        "numpy.dot(kernel, sens_data)".
    vertices : array | list of array
        Vertex numbers corresponding to the data.
    tmin : float
        Time point of the first sample in data.
    tstep : float
        Time step between successive samples in data.
    freqs : ndarray, shape (n_freqs,)
        The frequencies in Hz.
    dims : tuple, default ("dipoles", "freqs", "times")
        The dimension names of the data, where each element of the tuple
        corresponds to one axis of the data field. Allowed values are:
        ("dipoles", "freqs", "times"), ("dipoles", "epochs", "freqs",
        "times"), ("dipoles", "orientations", "freqs", "times"), ("dipoles",
         "orientations", "epochs", "freqs", "times").
    method : str | None, default None
        Comment on the method used to compute the data, as a combination of
        the used method and the compued product (e.g. "morlet-power" or
        "stockwell-itc").
    subject : str | None
        The subject name. While not necessary, it is safer to set the
        subject parameter to avoid analysis errors.
    %(verbose)s

    Attributes
    ----------
    freqs : ndarray, shape (n_freqs,)
        The frequencies in Hz.
    method : str | None, default None
        Comment on the method used to compute the data, as a combination of
        the used method and the compued product (e.g. "morlet-power" or
        "stockwell-itc").
    subject : str | None
        The subject name.
    times : array, shape (n_times,)
        The time vector.
    vertices : array | list of array of shape (n_dipoles,)
        The indices of the dipoles in the different source spaces. Can
        be an array if there is only one source space (e.g., for volumes).
    data : array of shape (n_dipoles, n_times)
        The data in source space.
    dims : tuple
        The dimension names corresponding to the data.
    shape : tuple
        The shape of the data. A tuple of int (n_dipoles, n_times).
    """

    @verbose
    def __init__(self, data, vertices=None, tmin=None, tstep=None, freqs=None,
                 dims=("dipoles", "freqs", "times"), method=None, subject=None,
                 verbose=None):

        valid_dims = [("dipoles", "freqs", "times"),
                      ("dipoles", "epochs", "freqs", "times"),
                      ("dipoles", "orientations", "freqs", "times"),
                      ("dipoles", "orientations", "epochs", "freqs", "times")]

        valid_methods = ["morlet-power", "multitaper-power", "stockwell-power",
                         "morlet-itc", "multitaper-itc", "stockwell-itc", None]

        # unfortunately, _check option does not work with the original tuples
        _check_option("dims", list(dims),
                      [list(v_dims) for v_dims in valid_dims])
        _check_option("method", method, valid_methods)
        _validate_type(vertices, (np.ndarray, list), "vertices")

        data, kernel, sens_data, vertices = self._prepare_data(data, vertices,
                                                               dims)

        self.dims = dims
        self.vertices = vertices
        self.method = method
        self.freqs = freqs
        self.verbose = verbose
        self.subject = _check_subject(None, subject, False)

        # TODO: src_type should rather represent the stc source type
        self._src_type = 'SourceTFR'
        self._data_ndim = len(dims)
        self._data = data
        self._kernel = kernel
        self._sens_data = sens_data
        self._kernel_removed = False
        self._tmin = tmin
        self._tstep = tstep
        self._times = None
        self._update_times()


    def __repr__(self):  # noqa: D105
        s = "{} vertices".format((sum(len(v) for v in self._vertices_list),))
        if self.subject is not None:
            s += ", subject : {}".format(self.subject)
        s += ", tmin : {} (ms)".format(1e3 * self.tmin)
        s += ", tmax : {} (ms)".format(1e3 * self.times[-1])
        s += ", tstep : {} (ms)".format(1e3 * self.tstep)
        s += ", data shape : {}".format(self.shape)
        return "<{0}  |  {1}>".format(type(self).__name__, s)

    def _prepare_data(self, data, vertices, dims):
        """Prepare the data for the SourceTFR init."""
        kernel, sens_data = None, None
        if isinstance(data, tuple):
            if len(data) != 2:
                raise ValueError('If data is a tuple it has to be length 2')
            kernel, sens_data = data
            data = None
            if kernel.shape[1] != sens_data.shape[0]:
                raise ValueError('kernel and sens_data have invalid '
                                 'dimensions')
            if sens_data.ndim != len(dims):
                raise ValueError('The sensor data must have {0} dimensions, '
                                 'got {1}'.format(len(dims), sens_data.ndim, ))
            # TODO: Make sure this is supported
            if 'orientations' in dims:
                raise ValueError('Multiple orientations are not supported for '
                                 'data=(kernel, sens_data) ')

        if isinstance(vertices, list):
            vertices = [np.asarray(v, int) for v in vertices]
            if any(np.any(np.diff(v.astype(int)) <= 0) for v in vertices):
                raise ValueError('Vertices must be ordered in increasing '
                                 'order.')

            n_src = sum([len(v) for v in vertices])

            if len(vertices) == 1:
                vertices = vertices[0]
        elif isinstance(vertices, np.ndarray):
            n_src = len(vertices)

        # safeguard the user against doing something silly
        if data is not None:
            if data.shape[0] != n_src:
                raise ValueError('Number of vertices ({0}) and stfr.shape[0] '
                                 '({1}) must match'.format(n_src,
                                                           data.shape[0]))
            if data.ndim != len(dims):
                raise ValueError('Data (shape {0}) must have {1} dimensions '
                                 'for SourceTFR with dims={2}'
                                 .format(data.shape, len(dims),
                                         dims))

            if "orientations" in dims and data.shape[1] != 3:
                raise ValueError('If multiple orientations are defined, '
                                 'stfr.shape[1] must be 3. Got '
                                 'shape[1] == {}'.format(data.shape[1]))

        return data, kernel, sens_data, vertices

    @property
    def _vertices_list(self):
        return self.vertices

    # TODO: also support loading data
    @verbose
    def save(self, fname, ftype='h5', verbose=None):
        """Save the full SourceTFR to an HDF5 file.

        Parameters
        ----------
        fname : string
            The file name to write the SourceTFR to, should end in
            '-stfr.h5'.
        ftype : string
            File format to use. Currently, the only allowed values is "h5".
        %(verbose_meth)s
        """

        # this message looks more informative to me than _check_option().
        if ftype != 'h5':
            raise ValueError('{} objects can only be written as HDF5 files.'
                             .format(self.__class__.__name__, ))
        fname = fname if fname.endswith('h5') else '{}-stfr.h5'.format(fname)
        write_hdf5(fname,
                   dict(vertices=self.vertices, data=self.data, tmin=self.tmin,
                        tstep=self.tstep, subject=self.subject,
                        src_type=self._src_type),
                   title='mnepython', overwrite=True)

    @property
    def sfreq(self):
        """Sample rate of the data."""
        return 1. / self.tstep

    def _remove_kernel_sens_data_(self):
        """Remove kernel and sensor space data and compute self._data."""
        if self._kernel is not None or self._sens_data is not None:
            self._kernel_removed = True
            self._data = np.tensordot(self._kernel, self._sens_data,
                                      axes=([-1], [0]))
            self._kernel = None
            self._sens_data = None

    def crop(self, tmin=None, tmax=None):
        """Restrict SourceTFR to a time interval.

        Parameters
        ----------
        tmin : float | None
            The first time point in seconds. If None the first present is used.
        tmax : float | None
            The last time point in seconds. If None the last present is used.
        """
        mask = _time_mask(self.times, tmin, tmax, sfreq=self.sfreq)
        self.tmin = self.times[np.where(mask)[0][0]]
        if self._kernel is not None and self._sens_data is not None:
            self._sens_data = self._sens_data[..., mask]
        else:
            self.data = self.data[..., mask]

        return self  # return self for chaining methods

    @verbose
    def resample(self, sfreq, npad='auto', window='boxcar', n_jobs=1,
                 verbose=None):
        """Resample data.

        Parameters
        ----------
        sfreq : float
            New sample rate to use.
        npad : int | str
            Amount to pad the start and end of the data.
            Can also be "auto" to use a padding that will result in
            a power-of-two size (can be much faster).
        window : string or tuple
            Window to use in resampling. See scipy.signal.resample.
        %(n_jobs)s
        %(verbose_meth)s

        Notes
        -----
        For some data, it may be more accurate to use npad=0 to reduce
        artifacts. This is dataset dependent -- check your data!

        Note that the sample rate of the original data is inferred from tstep.
        """
        # resampling in sensor instead of source space gives a somewhat
        # different result, so we don't allow it
        self._remove_kernel_sens_data_()

        o_sfreq = 1.0 / self.tstep
        self.data = resample(self.data, sfreq, o_sfreq, npad, n_jobs=n_jobs)

        # adjust indirectly affected variables
        self.tstep = 1.0 / sfreq
        return self

    @property
    def data(self):
        """create the SourceTFR data field.

        Parameters
        ----------
        %(verbose_meth)s

        Returns
        -------
        data : The source level time-frequency transformed data.

        """
        if self._data is None:
            # compute the solution the first time the data is accessed and
            # remove the kernel and sensor data
            self._remove_kernel_sens_data_()

            # we can't yet give full support for TFR complex conversion
            # if 'power' in self.method:
            #    self._data = (self._data * self._data.conj()).real

        return self._data

    @data.setter
    def data(self, value):
        value = np.asarray(value)
        if self._data is not None and value.ndim != self._data.ndim:
            raise ValueError('Data array should have {} dimensions.'
                             .format(self._data.ndim))

        # vertices can be a single number, so cast to ndarray
        if isinstance(self.vertices, list):
            n_verts = sum([len(v) for v in self.vertices])
        elif isinstance(self.vertices, np.ndarray):
            n_verts = len(self.vertices)
        else:
            raise ValueError('Vertices must be a list or numpy array')

        if value.shape[0] != n_verts:
            raise ValueError('The first dimension of the data array must '
                             'match the number of vertices ({0} != {1})'
                             .format(value.shape[0], n_verts))

        self._data = value
        self._update_times()

    @property
    def shape(self):
        """Shape of the data."""
        if self._data is None:
            return (self._kernel.shape[0],) + self._sens_data.shape[1:]

        else:
            return self._data.shape

    @property
    def tmin(self):
        """The first timestamp."""
        return self._tmin

    @tmin.setter
    def tmin(self, value):
        self._tmin = float(value)
        self._update_times()

    @property
    def tstep(self):
        """The change in time between two consecutive samples (1 / sfreq)."""
        return self._tstep

    @tstep.setter
    def tstep(self, value):
        if value <= 0:
            raise ValueError('.tstep must be greater than 0.')
        self._tstep = float(value)
        self._update_times()

    @property
    def times(self):
        """A timestamp for each sample."""
        return self._times

    @times.setter
    def times(self, value):
        raise RuntimeError('You cannot write to the .times attribute directly.'
                           ' This property automatically updates whenever '
                         '.tmin, .tstep or .data changes.')

    def _update_times(self):
        """Update the times attribute after changing tmin, tmax, or tstep."""
        self._times = self.tmin + (self.tstep * np.arange(self.shape[-1]))
        self._times.flags.writeable = False

    def copy(self):
        """Return copy of SourceTFR instance."""
        return copy.deepcopy(self)
