import numpy
import skimage
import matplotlib
import itertools

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

from contextlib import contextmanager

import skimage.filters
import skimage.morphology

from dumb.util import noraise

from dumb.util import bicyclelist

from .rois.roi import Roi
from .rois.pixelroi import PixelRoi
from .rois.polyroi import PolygonRoi
from .rois.branchroi import BranchRoi
from .rois.segmentroi import SegmentRoi

from .pixelmaskcreator import PixelMaskCreator
from .polymaskcreator import PolyMaskCreator
from .branchmaskcreator import BranchMaskCreator

from PyQt4 import QtGui


#TODO masks for branches with only one line aka 0 segments aka spines


class DendriteSegmentationTool(object):
    """
    The main class that is doing the event handling, organizes the gui and puts together the plot.
    """

    @property
    def threshold(self):
        return self.__threshold

    @threshold.setter
    def threshold(self,t):
        with self.disable_draw():
            self.__threshold = t
            elevation_map = skimage.filters.sobel(self.meandata)

            markers = numpy.zeros_like(self.meandata)
            markers[self.meandata < self.threshold] = 1
            markers[self.meandata > self.threshold*1.1] = 2
            segmentation = skimage.morphology.watershed(elevation_map, markers)

            overlay = numpy.zeros(shape = self.meandata.shape + (4,),dtype = float)
            overlay[...,3] = segmentation == 1
            if not hasattr(self,"overlayimg"):
                self.overlayimg = self.aximage.imshow(overlay,interpolation = "nearest")
            else:
                self.overlayimg.set_data(overlay)
            self.mask = segmentation == 2
            # force recalculation of traces
            PolygonRoi.tracecache.clear()
            # set proper ylimit for axtraceactive and axtracehold
            for ax in [self.axtraceactive] + self.axtracehold:
                if len(ax.lines) <= 1: continue # skip axes if it only contains one line (the frame marker)
                ymin,ymax = 0,0
                for l in ax.lines:
                    x,y = l.get_data()
                    # filter out the vertical frame marker line
                    if len(x) <= 2: continue
                    ymin = min(numpy.min(y),ymin)
                    ymax = max(numpy.max(y),ymax)
                ax.set_ylim(ymin*0.95,ymax*1.05)
        self.fig.canvas.draw()

    @property
    def show_overlay(self):
        return self.overlayimg.get_visible()

    @show_overlay.setter
    def show_overlay(self, v):
        # see if value changed
        b = self.show_overlay
        self.overlayimg.set_visible(v)
        if (b and not v) or (v and not b):
            self.fig.canvas.draw()

    def next_branch(self):
        if len(self.branches) > 0:
            self.active_roi = self.branches.next()

    def previous_branch(self):
        if len(self.branches) > 0:
            self.active_roi = self.branches.prev()

    def next_pixelroi(self):
        if len(self.pixelrois) > 0:
            self.active_roi = self.pixelrois.next()

    def next_polyroi(self):
        if len(self.polyrois) > 0:
            self.active_roi = self.polyrois.next()

    def previous_pixelroi(self):
        if len(self.pixelrois) > 0:
            self.active_roi = self.pixelrois.prev()

    def previous_polyroi(self):
        if len(self.polyrois) > 0:
            self.active_roi = self.polyrois.prev()

    def next_segment(self):
         if self.active_branch is not None:
            self.active_roi = self.active_branch.next_segment()

    def previous_segment(self):
        if self.active_branch is not None:
            self.active_roi = self.active_branch.previous_segment()

    @property
    def active_roi(self):
        """
        Return the active roi. This can be any of the following:
         - a branch (if the branch has no segments)
         - a segment of a branch (the corresponding active branch can be obtained by segment.parent)
         - a freehand polygon roi
         - a pixel based roi
         - None
        """
        if not hasattr(self, "_activeroi"):
            self._activeroi = None
        return self._activeroi


    @property
    def active_segment(self):
        if type(self.active_roi) is SegmentRoi:
            return self.active_roi
        else:
            return None

    @property
    def active_branch(self):
        if type(self.active_roi) is BranchRoi:
            return self.active_roi
        elif type(self.active_roi) is SegmentRoi:
            return self.active_roi.parent
        else:
            return None

    @property
    def active_polyroi(self):
        if type(self.active_roi) is PolygonRoi:
            return self.active_roi
        else:
            return None

    @property
    def active_pixelroi(self):
        if type(self.active_roi) is PixelRoi:
            return self.active_roi
        else:
            return None

    @property
    def active_frame(self):
        return self.__active_frame

    @active_roi.setter
    def active_roi(self,p):
        if self.active_roi is p:
            return
        with self.disable_draw():
            # TODO check if p is in any of the roi groups
            before = self.active_roi

            # disable previously active roi
            if self.active_roi is not None:
                self.active_roi.active = False

            # disable the branch if the active roi is not a segment or a segment of another branch
            if self.active_segment is not None:
                if not hasattr(p,"parent") or p.parent is not self.active_segment.parent:
                    self.active_segment.parent.active = False

            # set new active roi
            self._activeroi = p

            # enable new active roi
            if p is not None:
                self.active_roi.active = True

                # enable/disable hold buttons
                for axes,btn in zip(self.axtracehold,self.toolbar_tracehold.holdbuttons):
                    btn.setEnabled(p is not None)
                    checked = (p is not None) and (axes in p.holdaxes)
                    btn.setChecked(checked)

                # enable branch if active roi is a segment
                if self.active_segment is not None:
                    self.active_segment.parent.active = True

        self.fig.canvas.draw()


    @active_frame.setter
    def active_frame(self,f):
        with self.disable_draw():
            if not 0 <= f < self.data.shape[2]:
                raise Exception("Frame needs to be in range [0,{}]".format(self.data.shape[2]))

            self.__active_frame = f
            self.frameimg.set_data(self.data[...,f])

            # remove the markers
            if hasattr(self, "_DendriteSegmentationTool__active_frame_lines"):
                for l in self.__active_frame_lines:
                    l.remove()

            # redraw the markers
            self.__active_frame_lines = []
            for ax in self.timeaxes:
                l = ax.axvline(x = f, color = 'black', lw = 1.)
                self.__active_frame_lines.append(l)

        self.fig.canvas.draw()

    def toggle_overlay(self):
        self.show_overlay = not self.show_overlay

    def split_branches(self,length):
        with self.disable_draw():
            for b in self.branches:
                self.split_branch(length,b)
        self.fig.canvas.draw()

    def split_branch(self, length, branch = None):
        """
            Split one of the root branches into segments.
            Arguments: branch (defaults to active branch)
                       length (defaults to length from picker widget)
            Returns: nothing
        """
        branch = self.active_branch if branch is None else branch

        if branch is not None and branch in self.branches:
            branch.split(length = length)
        if branch.active:
            self.active_roi = branch.children[0]

    def split_segment(self,segment = None, parts = 2):
        """Split the given segment in to equal parts."""
        segment = self.active_segment if segment is None else segment

        # there might be no active segment
        if segment is None:
            return

        # get the index of the old segment in the parents child list
        i = segment.parent.children.index(segment)

        segment.split(nsegments = parts)
        if segment.parent.active:
            self.active_roi = segment.parent.children[i]

    def join_segments(self, segment = None, next = True):
        """
        Join two segments into one. Arguments:
            segment: A segment of any branch. Defaults to the active segment.
            next:    True or False, denote whether to join the segment with the preceeding or succeeding one.
        """
        segment = self.active_segment if segment is None else segment

        # there might be no active segment
        if segment is None:
            return

        # create and retrieve the new segment
        joined = segment.join(next = next)

        # make the new segment active
        if segment.parent.active:
            self.active_roi = joined

    @contextmanager
    def disable_draw(self):
        # store the original draw method
        draw = self.fig.canvas.draw

        def noop(*args):
            pass
            #print args
            #print "draw noop"

        # override the draw method as noop
        self.fig.canvas.draw = noop

        # yield and run code in context
        #print "<draw noop context"
        yield
        #print "<end draw noop context"

        # restore the original behaviour of draw
        self.fig.canvas.draw = draw


    def __init__(self, data, swc, mean = None, pmin = 10, pmax = 99):
        """
        Create and show the gui for data analysis.
        Args:
            data:  The 3D dataset
            swc:   SWC File that allows looping over branches.
            mean: Background image. Defaults to data.mean(axis = -1)
            pmin,pmax: Percentiles for color range. I.e. the color range for mean and data will start at pmin %
                           and reach up to pmax %. Defaults to (10,99)
        """
        self.data = data
        self.swc = swc
        self.meandata = numpy.mean(data,axis = -1) if mean is None else mean

        self.fig = plt.figure()

        self.gs = gridspec.GridSpec(2, 1, height_ratios = [.3,.7])
        self.axraster = plt.subplot(self.gs[0])
        self.gsl = gridspec.GridSpecFromSubplotSpec(4, 2, subplot_spec=self.gs[1], height_ratios = [.6,1,1,1], hspace = 0.075)
        self.aximage  = plt.subplot(self.gsl[:,1])
        self.axtraceactive  = plt.subplot(self.gsl[0,0],sharex = self.axraster)
        self.axhold1 = plt.subplot(self.gsl[1,0],sharex = self.axraster)
        self.axhold2 = plt.subplot(self.gsl[2,0],sharex = self.axraster)
        self.axhold3 = plt.subplot(self.gsl[3,0],sharex = self.axraster)

        self.axtracehold = [self.axhold1,self.axhold2,self.axhold3]
        """ a list with the axes where traces can put on hold"""
        self.timeaxes = [self.axtraceactive] + self.axtracehold + [self.axraster]
        """ a list with all axes that have time as x axis"""

        # disable labels in the hold axes two timeaxes and label the active axes
        self.axtraceactive.tick_params(axis = 'x', labelbottom = False, labeltop = True)

        for ax in self.axtracehold:
            ax.tick_params(axis = 'x', labelbottom = False)

        for ax in self.timeaxes:
            ax.set_xlim(0,data.shape[-1])

        dx = data.shape[1]*0.26666
        dy = data.shape[0]*0.26666
        vmin,vmax = numpy.percentile(self.meandata.flatten(), q = [pmin,pmax])
        self.meanimg  = self.aximage.imshow(self.meandata,cmap = matplotlib.cm.gray,
                                        interpolation='nearest',vmin = vmin,vmax = vmax)

        red_alpha_cm = matplotlib.cm.get_cmap('jet')
        red_alpha_cm._init()
        red_alpha_cm._lut[:,-1] = numpy.linspace(.0, 1.0, red_alpha_cm.N+3)
        #red_alpha_cm.set_under([0,0,0,0])

        #norm = matplotlib.colors.LogNorm(.001,1.)
        x,y,t = self.data.shape
        vmin,vmax = numpy.nanpercentile(self.data[...,:min(t/10,50)], q = [pmin,pmax])
        norm = matplotlib.colors.Normalize(vmin = vmin, vmax = vmax, clip = True)
        self.frameimg = self.aximage.imshow(self.data[...,0],cmap = red_alpha_cm,norm = norm ,
                                        interpolation='nearest')
        # disable autoscale on image axes, to avoid rescaling due to additional artists.
        self.aximage.set_autoscale_on(False)

        self.fig.colorbar(self.frameimg,ax = self.aximage)
        self.threshold    = numpy.percentile(self.meandata.flatten(), q = 90)
        self.active_frame = 0

        self.colors = ['#CC0099','#CC3300','#99CC00','#00FF00','#006600','#999966']
        self.colorcycle = itertools.cycle(self.colors)

        self.branches = bicyclelist()
        """ The list which stores the branches loaded from swc. This list should not be modified"""

        self.polyrois = bicyclelist()
        """ The list which stores the polymask rois. Use app.add_polymask(roi) and app.remove_polymask(roi) to modify list."""

        self.pixelrois = bicyclelist()
        """ The list which stores the pixel mask rois. Use app.add_pixelmask(roi) and app.remove_pixelmask(roi) to modify list."""

        # get all parts from the swc file that have at least one segment
        if swc is not None:
            for b in swc.branches:
                if len(b) > 1:
                    self.add_branchroi(b)

        self.fig.canvas.set_window_title('DendriteSegmentationTool')
        #self.fig.canvas.mpl_disconnect(self.fig.canvas.manager.key_press_handler_id)
        self.fig.canvas.mpl_connect('pick_event', noraise(self.onpick))
        self.fig.canvas.mpl_connect('key_press_event', noraise(self.onkey))
        self.fig.canvas.mpl_connect('button_press_event',noraise(self.onclick))

        self.toolbar_navigation = self.fig.canvas.manager.toolbar

        from .toolbars import NavigationToolbar
        self.toolbar_branch = NavigationToolbar(app = self)
        self.fig.canvas.manager.window.addToolBar(self.toolbar_branch)

        from .toolbars import MaskToolbar
        self.toolbar_mask = MaskToolbar(app = self)
        self.fig.canvas.manager.window.addToolBar(self.toolbar_mask)

        from .toolbars import SplitJoinToolbar
        self.toolbar_splitjoin = SplitJoinToolbar(app = self)
        self.fig.canvas.manager.window.addToolBar(self.toolbar_splitjoin)

        from .toolbars import ManageRoiToolbar
        self.toolbar_createroi = ManageRoiToolbar(app = self)
        self.fig.canvas.manager.window.addToolBar(self.toolbar_createroi)

        from .toolbars import TraceHoldToolbar
        self.toolbar_tracehold = TraceHoldToolbar(app = self)
        self.toolbar_tracehold.holdChanged.connect(self.toggle_hold)
        self.fig.canvas.manager.window.addToolBar(self.toolbar_tracehold)

        from .toolbars import PostTraceToolbar
        self.toolbar_postprocess = PostTraceToolbar(app = self)
        self.toolbar_postprocess.revalidate.connect(self.toggle_filter)
        self.fig.canvas.manager.window.addToolBar(self.toolbar_postprocess)

        # finally, select first branch
        self.next_branch()

    def post_apply(self,trace):
        """
        This is a callback function for the rois. It gets called after trace generation and is responsible for all
        post processing of traces.
        Args:
            trace: The raw trace of the roi
        Returns:
            trace: A postprocessed trace of the roi

        """

        import numpy
        import scipy.signal
        if self.toolbar_postprocess.toggle_detrend.isChecked():
            if not numpy.isinf(trace).any() and not numpy.isnan(trace).any():
                trace = scipy.signal.detrend(trace)
        if self.toolbar_postprocess.toggle_smoothen.isChecked():
            N = self.toolbar_postprocess.spin_smoothen.value()
            trace = numpy.convolve(trace, numpy.ones(shape=N), mode='same') / N
        return trace

    def toggle_filter(self):
        with self.disable_draw():
            Roi.tracecache.clear()
        self.fig.canvas.draw()

    def toggle_hold(self, ax):
        if type(ax) is int:
            ax = self.axtracehold[ax]
        print self.active_roi
        if self.active_roi is not None:
            self.active_roi.toggle_hold(ax)
            self.fig.canvas.draw()

    def add_branchroi(self, branch):
        """
        Add a new branch to the list of managed branches.
        Args:
            branch: The branch to add. Expected to be of type swc.Branch.
        """
        branchroi = BranchRoi(branch = branch, datasource = self, axes = self)
        self.branches.append(branchroi)
        self.fig.canvas.draw()

    def add_polyroi(self,x,y):
        polyroi = PolygonRoi(outline = numpy.array([x,y]).T,
                             axes = self, datasource = self)
        self.polyrois.append(polyroi)
        self.active_roi = self.polyrois[-1]

    def add_pixelroi(self,x,y):
        pixelroi = PixelRoi(pixels = [x,y],
                             axes = self, datasource = self)
        self.pixelrois.append(pixelroi)
        self.active_roi = self.pixelrois[-1]

    def remove_pixelroi(self,p = None):
        """remove given or active roi (if p is None) and make the next roi active."""
        if p is None:
            p = self.active_pixelroi
        if p is None:
            return
        p.remove()
        self.pixelrois.remove(p)
        self.active_roi = self.pixelrois.cur()

    def remove_polyroi(self,p = None):
        if p is None:
            p = self.active_polyroi
        if p is None:
            return
        p.remove()
        self.polyrois.remove(p)
        self.active_roi = self.polyrois.cur()

    def remove_branch(self, branch = None):
        if branch is None:
            branch = self.active_branch
        if branch is not None:
            selectnew = branch is self.active_roi
            branch.remove()
            self.branches.remove(branch)
            if selectnew:
                self.active_roi = self.branches.cur()

    def remove_roi(self,roi):
        selectnew = roi is self.active_roi
        if roi in self.branches:
            self.branches.remove(roi)
        elif roi in self.polyrois:
            self.polyrois.remove(roi)
        elif roi in self.pixelrois:
            self.pixelrois.remove(roi)
        else:
            raise Exception("The given roi is not managed by this Segmentation.")
        roi.remove()
        if selectnew:
            self.active_roi = None

    def onkey(self,event):
        if event.inaxes in self.timeaxes and event.key == ' ':
            self.active_frame = int(event.xdata)
        if event.key == '+' and not self.branchmask_creator.enabled:
            self.threshold = self.threshold*1.05
        if event.key == '-' and not self.branchmask_creator.enabled:
            self.threshold = self.threshold/1.05
        if event.key  == 'm':
            self.toggle_overlay()

    def onclick(self,event):
        if event.inaxes is self.axraster:
            if self.active_branch is not None:
                index = int(event.ydata)
                if index < len(self.active_branch.children):
                    self.active_roi = self.active_branch.children[index]


    def onpick(self,event):
        if event.mouseevent.inaxes is self.aximage:
            if event.artist.roi is self.active_roi:
                # ignore the selection event for the active item
                # TODO: this doesnt work properyl, since the segment gets activated bevore the freehand onpick is evaluated
                # hence in the freehand onpick evaluation we reactivate the freehand even if it was the active selection
                # bevore onpick invocation
                return
            if event.artist.roi in self.polyrois:
                #print "fount polyroi, ignoring"
                return
                #self.active_poly = event.artist.roi
            for b in self.branches:
                if event.artist.roi in b.children:
                    #print "fount segmentroi"
                    self.active_roi = event.artist.roi
        elif event.mouseevent.inaxes in [self.axtraceactive] + self.axtracehold:
            #print "onpick for trace", event.artist
            # get the roi from the selected line
            if hasattr(event.artist,"roi"):
                roi = event.artist.roi
                # check whether the roi is a segment or freehand
                if roi in self.polyrois:
                    # its a freehand, make it active
                    self.active_roi = roi
                else:
                    # seach for segment in all branche's children
                    for b in self.branches:
                        if roi in b.children:
                            # found it, make it active
                            self.active_roi = roi
                # nothing found, ignore it


    def save_hdf5(self, filename, mask = True, data = False):
        """
        Create a hdf5 file holding the overlay mask, the rois and the traces of the current setup.
        The structure of the hdf5 file will be as follows:
            - mask (dataset, optional, binary mask defined by threshold value, threshold is stored as attribute)
            - data (dataset, optional, the full 3D dataset from which the traces were generated)
            - branches (group holding subgroups for each branch)
                - 0 (group for an individual branch)
                    - roi (dataset, definition of the branch, (x,y,z,r) tuples)
                    - trace (dataset, trace of branch)
                    - linescan (dataset. combined traces of all children of the branch, only present if branch is segmented)
                    - outline (dataset, Nx2)
                    - segments (group holding segment subgroups)
                        - 0 (group for an individual segment)
                            - roi (dataset, definition of the segment, (x,y,z,r) tuples)
                            - trace (dataset, trace of segment)
                            - outline (dataset. Nx2)
                        - ... (more segments)
                - ... (more branches)
            - circles (group holding subgroups for each circle roi)
                - 0 (group for individual roi)
                    - roi (dataset, (x,y,r))
                    - trace (dataset)
                - ... (more circles)
            - polygons
                - 0 (group for individual roi)
                    - roi (dataset, the outline of the polygon, Nx2)
                    - trace (dataset)
                - ... (more polygons)
            - pixels
                - 0 (group for individual roi)
                    - roi (dataset, the pixel coordinates, shape: 2xN)
                    - trace (dataset)
                - ... (more pixels)
        Args:
            filename: filename to use, suffix ".h5" will be added if missing.
            mask: flag whether mask should be stored in file. default = True
            data: flag whether data should be stored in file. default = False
        """
        import h5py
        if '.' not in filename:
            filename = filename + '.h5'
        f = h5py.File(filename,mode = 'w')
        f.clear()
        if mask:
            f.create_dataset('mask',data = self.mask)
            f['mask'].attrs['threshold'] = self.threshold
        if data:
            f.create_dataset('data', data = self.data)

        print f.create_group('pixels')
        for i,m in enumerate(self.pixelrois):
            f.create_dataset('pixels/'+str(i) + '/roi', data = m.pixels)
            f.create_dataset('pixels/'+str(i) + '/trace', data = m.trace)

        print f.create_group('polygons')
        for i,m in enumerate(self.polyrois):
            f.create_dataset('polygons/'+str(i) + '/roi',data = m.outline)
            f.create_dataset('polygons/'+str(i) + '/trace',data = m.trace)

        print f.create_group('circles')
        for i,m in enumerate(getattr(self,"circlerois",[])):
            f.create_dataset('circles/'+str(i) + '/roi',  data = [m.x,m.y,m.r])
            f.create_dataset('circles/'+str(i) + '/trace',data = m.trace)

        print f.create_group('branches')
        for i,b in enumerate(self.branches):
            f.create_group('branches/{}'.format(i))
            f.create_dataset('branches/{}/roi'.format(i),    data = b.branch)
            f.create_dataset('branches/{}/outline'.format(i),data = b.outline)
            f.create_dataset('branches/{}/trace'.format(i),  data = b.trace)
            if len(b.children) > 0:
                f.create_dataset('branches/{}/linescan'.format(i),data = b.linescan)
            f.create_group('branches/{}/segments'.format(i))
            for j,s in enumerate(b.children):
                f.create_group('branches/{}/segments/{}'.format(i,j))
                f.create_dataset('branches/{}/segments/{}/roi'.format(i,j),    data = s.branch)
                f.create_dataset('branches/{}/segments/{}/outline'.format(i,j),data = s.outline)
                f.create_dataset('branches/{}/segments/{}/trace'.format(i,j),  data = s.trace)
        # write stuff to disc
        f.close()


    #TODO status bar which denotes the roi which is active (number and type)

    # TODO
    def load_hdf5(self,filename, data = False, mask = True, branches = True, circles = True):
        """

        Args:
            filename:
            mask:
            branches:
            circles:

        Returns:

        """