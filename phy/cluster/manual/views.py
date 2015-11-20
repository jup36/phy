# -*- coding: utf-8 -*-

"""Manual clustering views."""


# -----------------------------------------------------------------------------
# Imports
# -----------------------------------------------------------------------------

import logging

import numpy as np
from matplotlib.colors import hsv_to_rgb, rgb_to_hsv
from six import string_types

from phy.io.array import _index_of, _get_padded
from phy.electrode.mea import linear_positions
from phy.gui import Actions
from phy.plot import (BoxedView, StackedView, GridView,
                      _get_linear_x)
from phy.plot.utils import _get_boxes
from phy.stats import correlograms
from phy.utils._types import _is_integer

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Utils
# -----------------------------------------------------------------------------

# Default color map for the selected clusters.
_COLORMAP = np.array([[8, 146, 252],
                      [255, 2, 2],
                      [240, 253, 2],
                      [228, 31, 228],
                      [2, 217, 2],
                      [255, 147, 2],
                      [212, 150, 70],
                      [205, 131, 201],
                      [201, 172, 36],
                      [150, 179, 62],
                      [95, 188, 122],
                      [129, 173, 190],
                      [231, 107, 119],
                      ])


def _selected_clusters_colors(n_clusters=None):
    if n_clusters is None:
        n_clusters = _COLORMAP.shape[0]
    if n_clusters > _COLORMAP.shape[0]:
        colors = np.tile(_COLORMAP, (1 + n_clusters // _COLORMAP.shape[0], 1))
    else:
        colors = _COLORMAP
    return colors[:n_clusters, ...] / 255.


def _extract_wave(traces, spk, mask, wave_len=None):
    n_samples, n_channels = traces.shape
    if not (0 <= spk < n_samples):
        raise ValueError()
    assert mask.shape == (n_channels,)
    channels = np.nonzero(mask > .1)[0]
    # There should be at least one non-masked channel.
    if not len(channels):
        return
    i = spk - wave_len // 2
    j = spk + wave_len // 2
    a, b = max(0, i), min(j, n_samples - 1)
    data = traces[a:b, channels]
    data = _get_padded(data, i - a, i - a + wave_len)
    assert data.shape == (wave_len, len(channels))
    return data, channels


def _get_data_bounds(arr, n_spikes=None, percentile=None):
    n = arr.shape[0]
    k = max(1, n // n_spikes)
    w = np.abs(arr[::k])
    n = w.shape[0]
    w = w.reshape((n, -1))
    w = w.max(axis=1)
    m = np.percentile(w, percentile)
    return [-1, -m, +1, +m]


def _get_spike_clusters_rel(spike_clusters, spike_ids, cluster_ids):
    # Relative spike clusters.
    # NOTE: the order of the clusters in cluster_ids matters.
    # It will influence the relative index of the clusters, which
    # in return influence the depth.
    spike_clusters = spike_clusters[spike_ids]
    assert np.all(np.in1d(spike_clusters, cluster_ids))
    spike_clusters_rel = _index_of(spike_clusters, cluster_ids)
    return spike_clusters_rel


def _get_depth(masks, spike_clusters_rel=None, n_clusters=None):
    """Return the OpenGL z-depth of vertices as a function of the
    mask and cluster index."""
    n_spikes = len(masks)
    assert masks.shape == (n_spikes,)
    depth = (-0.1 - (spike_clusters_rel + masks) /
             float(n_clusters + 10.))
    depth[masks <= 0.25] = 0
    assert depth.shape == (n_spikes,)
    return depth


def _get_color(masks, spike_clusters_rel=None, n_clusters=None):
    """Return the color of vertices as a function of the mask and
    cluster index."""
    n_spikes = len(masks)
    assert masks.shape == (n_spikes,)
    assert spike_clusters_rel.shape == (n_spikes,)
    # Generate the colors.
    colors = _selected_clusters_colors(n_clusters)
    # Color as a function of the mask.
    color = colors[spike_clusters_rel]
    hsv = rgb_to_hsv(color[:, :3])
    # Change the saturation and value as a function of the mask.
    hsv[:, 1] *= masks
    hsv[:, 2] *= .5 * (1. + masks)
    color = hsv_to_rgb(hsv)
    color = np.c_[color, .5 * np.ones((n_spikes, 1))]
    return color


# -----------------------------------------------------------------------------
# Waveform view
# -----------------------------------------------------------------------------

class WaveformView(BoxedView):
    normalization_percentile = .95
    normalization_n_spikes = 1000
    overlap = True

    default_shortcuts = {
        'toggle_waveform_overlap': 'o',
    }

    def __init__(self,
                 waveforms=None,
                 masks=None,
                 spike_clusters=None,
                 channel_positions=None,
                 shortcuts=None,
                 keys='interactive',
                 ):
        """

        The channel order in waveforms needs to correspond to the one
        in channel_positions.

        """

        # Load default shortcuts, and override with any user shortcuts.
        self.shortcuts = self.default_shortcuts.copy()
        self.shortcuts.update(shortcuts or {})

        self._cluster_ids = None
        self._spike_ids = None

        # Initialize the view.
        if channel_positions is None:
            channel_positions = linear_positions(self.n_channels)
        box_bounds = _get_boxes(channel_positions)
        super(WaveformView, self).__init__(box_bounds, keys=keys)

        # Waveforms.
        assert waveforms.ndim == 3
        self.n_spikes, self.n_samples, self.n_channels = waveforms.shape
        self.waveforms = waveforms

        # Waveform normalization.
        self.data_bounds = _get_data_bounds(waveforms,
                                            self.normalization_n_spikes,
                                            self.normalization_percentile)

        # Masks.
        self.masks = masks

        # Spike clusters.
        assert spike_clusters.shape == (self.n_spikes,)
        self.spike_clusters = spike_clusters

        # Channel positions.
        assert channel_positions.shape == (self.n_channels, 2)
        self.channel_positions = channel_positions

        # Initialize the subplots.
        self._plots = {ch: self[ch].plot(x=[], y=[])
                       for ch in range(self.n_channels)}
        self.build()
        self.update()

    def on_select(self, cluster_ids, spike_ids):
        n_clusters = len(cluster_ids)
        n_spikes = len(spike_ids)
        if n_spikes == 0:
            return

        self._cluster_ids = cluster_ids
        self._spike_ids = spike_ids

        # Relative spike clusters.
        spike_clusters_rel = _get_spike_clusters_rel(self.spike_clusters,
                                                     spike_ids,
                                                     cluster_ids)

        # Fetch the waveforms.
        w = self.waveforms[spike_ids]
        t = _get_linear_x(n_spikes, self.n_samples)
        # Overlap.
        if self.overlap:
            t = t + 2.5 * (spike_clusters_rel[:, np.newaxis] -
                           (n_clusters - 1) / 2.)

        # Depth as a function of the cluster index and masks.
        masks = self.masks[spike_ids]

        # Plot all waveforms.
        # OPTIM: avoid the loop.
        for ch in range(self.n_channels):
            m = masks[:, ch]
            depth = _get_depth(m,
                               spike_clusters_rel=spike_clusters_rel,
                               n_clusters=n_clusters)
            color = _get_color(m,
                               spike_clusters_rel=spike_clusters_rel,
                               n_clusters=n_clusters)
            self._plots[ch].set_data(x=t, y=w[:, :, ch],
                                     color=color,
                                     depth=depth,
                                     data_bounds=self.data_bounds,
                                     )

        self.build()
        self.update()

    def attach(self, gui):
        """Attach the view to the GUI."""

        # Disable keyboard pan so that we can use arrows as global shortcuts
        # in the GUI.
        self.panzoom.enable_keyboard_pan = False

        gui.add_view(self)

        gui.connect_(self.on_select)
        # gui.connect_(self.on_cluster)

        self.actions = Actions(gui, default_shortcuts=self.shortcuts)
        self.actions.add(self.toggle_waveform_overlap)

    def toggle_waveform_overlap(self):
        self.overlap = not self.overlap
        self.on_select(self._cluster_ids, self._spike_ids)


# -----------------------------------------------------------------------------
# Trace view
# -----------------------------------------------------------------------------

class TraceView(StackedView):
    def __init__(self,
                 traces=None,
                 sample_rate=None,
                 spike_times=None,
                 spike_clusters=None,
                 masks=None,
                 n_samples_per_spike=None,
                 ):

        # Sample rate.
        assert sample_rate > 0
        self.sample_rate = sample_rate

        # Traces.
        assert traces.ndim == 2
        self.n_samples, self.n_channels = traces.shape
        self.traces = traces

        # Number of samples per spike.
        self.n_samples_per_spike = (n_samples_per_spike or
                                    int(.002 * sample_rate))

        # Spike times.
        if spike_times is not None:
            spike_times = np.asarray(spike_times)
            self.n_spikes = len(spike_times)
            assert spike_times.shape == (self.n_spikes,)
            self.spike_times = spike_times

            # Spike clusters.
            if spike_clusters is None:
                spike_clusters = np.zeros(self.n_spikes)
            assert spike_clusters.shape == (self.n_spikes,)
            self.spike_clusters = spike_clusters

            # Masks.
            assert masks.shape == (self.n_spikes, self.n_channels)
            self.masks = masks
        else:
            self.spike_times = self.spike_clusters = self.masks = None

        # Initialize the view.
        super(TraceView, self).__init__(self.n_channels)

        # TODO: choose the interval.
        self.set_interval((0., .25))

    def _load_traces(self, interval):
        """Load traces in an interval (in seconds)."""

        start, end = interval

        i, j = int(self.sample_rate * start), int(self.sample_rate * end)
        traces = self.traces[i:j, :]

        # Detrend the traces.
        m = np.mean(traces[::10, :], axis=0)
        traces -= m

        # Create the plots.
        return traces

    def _load_spikes(self, interval):
        assert self.spike_times is not None
        # Keep the spikes in the interval.
        a, b = self.spike_times.searchsorted(interval)
        return self.spike_times[a:b], self.spike_clusters[a:b], self.masks[a:b]

    def set_interval(self, interval):

        start, end = interval
        color = (.5, .5, .5, 1)

        dt = 1. / self.sample_rate

        # Load traces.
        traces = self._load_traces(interval)
        n_samples = traces.shape[0]
        assert traces.shape[1] == self.n_channels

        m, M = traces.min(), traces.max()
        data_bounds = [start, m, end, M]

        # Generate the trace plots.
        # TODO OPTIM: avoid the loop and generate all channel traces in
        # one pass with NumPy (but need to set a_box_index manually too).
        # t = _get_linear_x(1, traces.shape[0])
        t = start + np.arange(n_samples) * dt
        for ch in range(self.n_channels):
            self[ch].plot(t, traces[:, ch], color=color,
                          data_bounds=data_bounds)

        # Display the spikes.
        if self.spike_times is not None:
            wave_len = self.n_samples_per_spike
            spike_times, spike_clusters, masks = self._load_spikes(interval)
            n_spikes = len(spike_times)
            dt = 1. / float(self.sample_rate)
            dur_spike = wave_len * dt
            trace_start = int(self.sample_rate * start)

            # ac = Accumulator()
            for i in range(n_spikes):
                sample_rel = (int(spike_times[i] * self.sample_rate) -
                              trace_start)
                mask = self.masks[i]
                # clu = spike_clusters[i]
                w, ch = _extract_wave(traces, sample_rel, mask, wave_len)
                n_ch = len(ch)
                t0 = spike_times[i] - dur_spike / 2.
                color = (1, 0, 0, 1)
                box_index = np.repeat(ch[:, np.newaxis], wave_len, axis=0)
                t = t0 + dt * np.arange(wave_len)
                t = np.tile(t, (n_ch, 1))
                self.plot(t, w.T, color=color, box_index=box_index,
                          data_bounds=data_bounds)

        self.build()
        self.update()


# -----------------------------------------------------------------------------
# Feature view
# -----------------------------------------------------------------------------

def _check_dimension(dim, n_channels, n_features):
    """Check that a dimension is valid."""
    if _is_integer(dim):
        dim = (dim, 0)
    if isinstance(dim, tuple):
        assert len(dim) == 2
        channel, feature = dim
        assert _is_integer(channel)
        assert _is_integer(feature)
        assert 0 <= channel < n_channels
        assert 0 <= feature < n_features
    elif isinstance(dim, string_types):
        assert dim == 'time'
    elif dim:
        raise ValueError('{0} should be (channel, feature) '.format(dim) +
                         'or one of the extra features.')


def _dimensions_matrix(x_channels, y_channels):
    """Dimensions matrix."""
    # time, depth     time,    (x, 0)     time,    (y, 0)     time, (z, 0)
    # time, (x', 0)   (x', 0), (x, 0)     (x', 1), (y, 0)     (x', 2), (z, 0)
    # time, (y', 0)   (y', 0), (x, 1)     (y', 1), (y, 1)     (y', 2), (z, 1)
    # time, (z', 0)   (z', 0), (x, 2)     (z', 1), (y, 2)     (z', 2), (z, 2)

    n = len(x_channels)
    assert len(y_channels) == n
    y_dim = {}
    x_dim = {}
    # TODO: extra feature like probe depth
    x_dim[0, 0] = 'time'
    y_dim[0, 0] = 'time'

    # Time in first column and first row.
    for i in range(1, n + 1):
        x_dim[0, i] = 'time'
        y_dim[0, i] = (x_channels[i - 1], 0)
        x_dim[i, 0] = 'time'
        y_dim[i, 0] = (y_channels[i - 1], 0)

    for i in range(1, n + 1):
        for j in range(1, n + 1):
            x_dim[i, j] = (x_channels[i - 1], j - 1)
            y_dim[i, j] = (y_channels[j - 1], i - 1)

    return x_dim, y_dim


def _dimensions_for_clusters(cluster_ids, n_cols=None,
                             best_channels_func=None):
    """Return the dimension matrix for the selected clusters."""
    n = len(cluster_ids)
    if not n:
        return {}, {}
    best_channels_func = best_channels_func or (lambda _: range(n_cols))
    x_channels = best_channels_func(cluster_ids[min(1, n - 1)])
    y_channels = best_channels_func(cluster_ids[0])
    y_channels = y_channels[:n_cols - 1]
    # For the x axis, remove the channels that already are in
    # the y axis.
    x_channels = [c for c in x_channels if c not in y_channels]
    # Now, select the right number of channels in the x axis.
    x_channels = x_channels[:n_cols - 1]
    if len(x_channels) < n_cols - 1:
        x_channels = y_channels
    return _dimensions_matrix(x_channels, y_channels)


def _smart_dim(dim, n_features=None, prev_dim=None, prev_dim_other=None):
    channel, feature = dim
    prev_channel, prev_feature = prev_dim
    # Scroll the feature if the channel is the same.
    if prev_channel == channel:
        feature = (prev_feature + 1) % n_features
    # Scroll the feature if it is the same than in the other axis.
    if (prev_dim_other != 'time' and
            prev_dim_other == (channel, feature)):
        feature = (feature + 1) % n_features
    dim = (channel, feature)


def _project_mask_depth(dim, masks, spike_clusters_rel=None, n_clusters=None):
    """Return the mask and depth vectors for a given dimension."""
    n_spikes = masks.shape[0]
    if dim != 'time':
        ch, fet = dim
        m = masks[:, ch]
        d = _get_depth(m,
                       spike_clusters_rel=spike_clusters_rel,
                       n_clusters=n_clusters)
    else:
        m = np.ones(n_spikes)
        d = np.zeros(n_spikes)
    return m, d


class FeatureView(GridView):
    normalization_percentile = .95
    normalization_n_spikes = 1000

    def __init__(self,
                 features=None,
                 masks=None,
                 spike_times=None,
                 spike_clusters=None,
                 keys='interactive',
                 ):

        assert features.ndim == 3
        self.n_spikes, self.n_channels, self.n_features = features.shape
        self.n_cols = self.n_features + 1
        self.features = features

        # Initialize the view.
        super(FeatureView, self).__init__(self.n_cols, self.n_cols, keys=keys)

        # Feature normalization.
        self.data_bounds = _get_data_bounds(features,
                                            self.normalization_n_spikes,
                                            self.normalization_percentile)

        # Masks.
        self.masks = masks

        # Spike clusters.
        assert spike_clusters.shape == (self.n_spikes,)
        self.spike_clusters = spike_clusters

        # Spike times.
        assert spike_times.shape == (self.n_spikes,)
        self.spike_times = spike_times

        # Initialize the subplots.
        self._plots = {(i, j): self[i, j].scatter(x=[], y=[], size=[])
                       for i in range(self.n_cols)
                       for j in range(self.n_cols)
                       }
        self.build()
        self.update()

    def _get_feature(self, dim, spike_ids=None):
        f = self.features[spike_ids]
        assert f.ndim == 3

        if dim == 'time':
            t = self.spike_times[spike_ids]
            t0, t1 = self.spike_times[0], self.spike_times[-1]
            t = -1 + 2 * (t - t0) / float(t1 - t0)
            return .9 * t
        else:
            assert len(dim) == 2
            ch, fet = dim
            # TODO: normalization of features
            return f[:, ch, fet]

    def on_select(self, cluster_ids, spike_ids):
        n_clusters = len(cluster_ids)
        n_spikes = len(spike_ids)
        if n_spikes == 0:
            return

        masks = self.masks[spike_ids]
        sc = _get_spike_clusters_rel(self.spike_clusters,
                                     spike_ids,
                                     cluster_ids)

        x_dim, y_dim = _dimensions_for_clusters(cluster_ids,
                                                n_cols=self.n_cols,
                                                # TODO
                                                best_channels_func=None)

        # Plot all features.
        # TODO: optim: avoid the loop.
        for i in range(self.n_cols):
            for j in range(self.n_cols):

                x = self._get_feature(x_dim[i, j], spike_ids)
                y = self._get_feature(y_dim[i, j], spike_ids)

                mx, dx = _project_mask_depth(x_dim[i, j], masks,
                                             spike_clusters_rel=sc,
                                             n_clusters=n_clusters)
                my, dy = _project_mask_depth(y_dim[i, j], masks,
                                             spike_clusters_rel=sc,
                                             n_clusters=n_clusters)

                d = np.maximum(dx, dy)
                m = np.maximum(mx, my)

                color = _get_color(m,
                                   spike_clusters_rel=sc,
                                   n_clusters=n_clusters)

                self._plots[i, j].set_data(x=x,
                                           y=y,
                                           color=color,
                                           depth=d,
                                           data_bounds=self.data_bounds,
                                           size=5 * np.ones(n_spikes),
                                           )

        self.build()
        self.update()


# -----------------------------------------------------------------------------
# Correlogram view
# -----------------------------------------------------------------------------

class CorrelogramView(GridView):
    def __init__(self,
                 spike_times=None,
                 spike_clusters=None,
                 sample_rate=None,
                 bin_size=None,
                 window_size=None,
                 excerpt_size=None,
                 n_excerpts=None,
                 keys='interactive',
                 ):

        assert sample_rate > 0
        self.sample_rate = sample_rate

        assert bin_size > 0
        self.bin_size = bin_size

        assert window_size > 0
        self.window_size = window_size

        # TODO: excerpt

        self.spike_times = np.asarray(spike_times)
        self.n_spikes, = self.spike_times.shape

        # Initialize the view.
        self.n_cols = 2  # TODO: dynamic grid shape in interact
        super(CorrelogramView, self).__init__(self.n_cols, self.n_cols,
                                              keys=keys)

        # Spike clusters.
        assert spike_clusters.shape == (self.n_spikes,)
        self.spike_clusters = spike_clusters

        # Initialize the subplots.
        self._plots = {(i, j): self[i, j].hist(hist=[])
                       for i in range(self.n_cols)
                       for j in range(self.n_cols)
                       }
        self.build()
        self.update()

    def on_select(self, cluster_ids, spike_ids):
        n_clusters = len(cluster_ids)
        n_spikes = len(spike_ids)
        if n_spikes == 0:
            return

        ccg = correlograms(self.spike_times,
                           self.spike_clusters,
                           cluster_ids=cluster_ids,
                           sample_rate=self.sample_rate,
                           bin_size=self.bin_size,
                           window_size=self.window_size,
                           )

        lim = ccg.max()

        colors = _selected_clusters_colors(n_clusters)

        for i in range(n_clusters):
            for j in range(n_clusters):
                hist = ccg[i, j, :]
                color = colors[i] if i == j else np.ones(3)
                color = np.hstack((color, [1]))
                self._plots[i, j].set_data(hist=hist,
                                           color=color,
                                           ylim=[lim],
                                           )

        self.build()
        self.update()
