"""
26 Nov 2012

"""

from os.path                           import exists
from pytadbit.boundary_aligner.aligner import align
from pytadbit                          import tadbit
from pytadbit.utils.extraviews         import tadbit_savefig
from pytadbit.utils.extraviews         import _tad_density_plot
from pytadbit.experiment               import Experiment
from pytadbit.utils.hic_filtering      import hic_filtering_for_modelling
from string                            import ascii_lowercase as letters
from copy                              import deepcopy as copy
from cPickle                           import load, dump
from pytadbit.alignment                import Alignment, randomization_test
from numpy                             import log2
from random                            import random
from math                              import sqrt
from sys                               import stderr

try:
    import matplotlib.pyplot as plt
except ImportError:
    stderr.write('matplotlib not found\n')


def load_chromosome(in_f, fast=2):
    """
    Load a Chromosome object from a file. A Chromosome object can be saved with
    the :func:`Chromosome.save_chromosome` function.

    :param in_f: path to a saved Chromosome object file
    :param 2 fast: if fast=2 do not load the Hi-C data (in the case that they
       were saved in a separate file see :func:`Chromosome.save_chromosome`).
       If fast is equal to 1, the weights will be skipped from load to save
       memory. Finally if fast=0, both the weights and Hi-C data will be loaded

    :returns: a Chromosome object

    TODO: remove first try/except type error... this is loading old experiments
    """
    dico = load(open(in_f))
    name = ''
    crm = Chromosome(dico['name'])
    try:
        exp_order = dico['experiment_order']
    except KeyError:
        exp_order = dico['experiments'].keys()
    for name in exp_order:
        xpr = Experiment(name, dico['experiments'][name]['resolution'], 
                         no_warn=True)
        xpr.tads        = dico['experiments'][name]['tads']
        xpr.norm        = dico['experiments'][name]['wght']
        xpr.hic_data    = dico['experiments'][name]['hi-c']
        xpr.conditions  = dico['experiments'][name]['cond']
        xpr.size        = dico['experiments'][name]['size']
        xpr._zeros      = dico['experiments'][name].get('zero', {})
        try:  # new in version post-CSDM13
            xpr.identifier  = dico['experiments'][name]['iden']
            xpr.cell_type   = dico['experiments'][name]['cell']
            xpr.exp_type    = dico['experiments'][name]['expt']
            xpr.enzyme      = dico['experiments'][name]['enzy']
            xpr.description = dico['experiments'][name]['desc']
        except KeyError:
            xpr.identifier  = None
            xpr.cell_type   = None
            xpr.exp_type    = None
            xpr.enzyme      = None
            xpr.description = {}
        try:
            crm.experiments.append(xpr)
        except TypeError:
            continue
    crm.size            = dico['size']
    crm.r_size          = dico['r_size']
    crm.max_tad_size    = dico['max_tad_size']
    crm.forbidden       = dico['forbidden']
    crm._centromere     = dico['_centromere']
    try:  # new in version post-CSDM13
        crm.species         = dico['species']
        crm.assembly        = dico['assembly']
        crm.description     = dico['description']
    except KeyError:
        crm.species         = None
        crm.assembly        = None
        crm.description     = {}
    if isinstance(dico['experiments'][name]['hi-c'], str) or fast != int(2):
        try:
            dicp = load(open(in_f + '_hic'))
        except IOError:
            raise Exception('ERROR: file %s not found\n' % (
                dico['experiments'][name]['hi-c']))
        for name in dico['experiments']:
            crm.get_experiment(name).hic_data = dicp[name]['hi-c']
            if fast != 1:
                crm.get_experiment(name).norm = dicp[name]['wght']
    elif not fast:
        stderr.write('WARNING: data not saved correctly for fast loading.\n')
    return crm


class Chromosome(object):
    """
    A Chromosome object designed to deal with Topologically Associating Domains
    predictions from different experiments, in different cell types for a given
    chromosome of DNA, and to compare them.

    :param name: name of the chromosome (might be a chromosome name for example)
    :param None species: species name
    :param None assembly: version number of the genomic assembly used
    :param None resolutions: list of resolutions corresponding to a list of
       experiments passed.
    :param None experiment_hic_data: :py:func:`list` of paths to files
       containing the Hi-C count matrices corresponding to different experiments
    :param None experiment_tads: :py:func:`list` of paths to files
       containing the definition of TADs corresponding to different experiments
    :param None experiment_names: :py:func:`list` of the names of each 
        experiment
    :param infinite max_tad_size: maximum TAD size allowed. TADs longer than
        this value will not be considered, and size of the corresponding
        chromosome size will be reduced accordingly
    :param 0 chr_len: size of the DNA chromosome in bp. By default it will be
        inferred from the distribution of TADs
    :param None parser: a parser function that returns a tuple of lists 
       representing the data matrix and the length of a row/column. With
       the file example.tsv:

       ::
       
         chrT_001	chrT_002	chrT_003	chrT_004
         chrT_001	629	164	88	105
         chrT_002	164	612	175	110
         chrT_003	88	175	437	100
         chrT_004	105	110	100	278

       the output of parser('example.tsv') would be be:
       ``[([629, 164, 88, 105, 164, 612, 175, 110, 88, 175, 437, 100, 105,
       110, 100, 278]), 4]``
    :param None kw_descr: any other argument passed would be stored as
       complementary descriptive field. For example::
       
           crm  = Chromosome('19', species='mus musculus',
                             subspecies='musculus musculus',
                             skin_color='black')
           print crm

           # Chromosome 19:
           #    0  experiment loaded: 
           #    0  alignment loaded: 
           #    species         : mus musculus
           #    assembly version: UNKNOWN
           #    subspecies      : musculus musculus
           #    skin_color      : black

       *note that these fields may appear in the header of generated out files*

    :return: Chromosome object


    """
    def __init__(self, name, species=None, assembly=None,
                 experiment_resolutions=None, experiment_tads=None,
                 experiment_hic_data=None, experiment_norm_data=None,
                 experiment_names=None, max_tad_size=float('inf'),
                 chr_len=0, parser=None, centromere_search=False,
                 silent=False, **kw_descr):
        self.name             = name
        self.size             = self._given_size = self.r_size = chr_len
        self.size             = ChromosomeSize(self.size)
        self.max_tad_size     = max_tad_size
        self.r_size           = RelativeChromosomeSize(self.size)
        self.forbidden        = {}  # only used for TAD alignment randomization
        self.experiments      = ExperimentList([], self)
        self._centromere      = None
        self.alignment        = AlignmentDict()
        self.description      = kw_descr
        self.species          = species
        self.assembly         = assembly

        self._search_centromere = centromere_search
        if experiment_tads:
            for i, handler in enumerate(experiment_tads or []):
                name = experiment_names[i] if experiment_names else None
                self.add_experiment(name, experiment_resolutions[i],
                                    tad_def=handler, parser=parser)
        if experiment_hic_data:
            for i, handler in enumerate(experiment_hic_data or []):
                name = experiment_names[i] if experiment_names else None
                try:
                    xpr = self.get_experiment(name)
                    xpr.load_hic_data(handler, silent=silent)
                    continue
                except:
                    pass
                if isinstance(handler, Experiment):
                    handler.name = name or handler.name
                    self.experiments.append(handler)
                else:
                    self.add_experiment(name, experiment_resolutions[i],
                                        hic_data=handler, parser=parser,
                                        silent=silent)
        if experiment_norm_data:
            for i, handler in enumerate(experiment_norm_data or []):
                name = experiment_names[i] if experiment_names else None
                try:
                    xpr = self.get_experiment(name)
                    xpr.load_norm_data(handler, silent=silent)
                    continue
                except:
                    pass
                if isinstance(handler, Experiment):
                    handler.name = name or handler.name
                    self.experiments.append(handler)
                else:
                    self.add_experiment(name, experiment_resolutions[i],
                                        norm_data=handler, parser=parser,
                                        silent=silent)

    def __repr__(self):
        outstr = 'Chromosome %s:\n' % self.name
        outstr += ('   %-2s experiment%s loaded: ' % (
            len(self.experiments), 's' * (len(self.experiments) > 0)) +
                   ', '.join([e.name for e in self.experiments]) + '\n')
        outstr += ('   %-2s alignment%s loaded: ' % (
            len(self.alignment), 's' * (len(self.alignment) > 0)) +
                   ', '.join([a.name for a in self.alignment]) + '\n')
        try:  # new in version post-CSDM13
            outstr += '   species         : %s\n' % (self.species or 'UNKNOWN')
            outstr += '   assembly version: %s\n' % (self.assembly or 'UNKNOWN')
            for desc in self.description:
                outstr += '   %-16s: %s\n' % (desc, self.description[desc])
        except AttributeError:
            pass
        return outstr


    def _get_forbidden_region(self, xpr, resized=False):
        """
        Find the regions for which there is no information in any of the
        experiments. This is used to infer the relative chromosome size.
        """
        if not xpr.tads:
            return
        forbidden = []
        for pos in xpr.tads:
            start = float(xpr.tads[pos]['start'])
            end   = float(xpr.tads[pos]['end'])
            diff  = end - start
            if diff * xpr.resolution > self.max_tad_size:
                forbidden += range(int(start), int(end+1))
                xpr.tads[pos]['score'] = -abs(xpr.tads[pos]['score'])
            else:
                xpr.tads[pos]['score'] = abs(xpr.tads[pos]['score'])
        if not self.forbidden:
            self.forbidden = dict([(f, None) for f in forbidden])
        else:
            self.forbidden = dict([(f, None) for f in 
                                   set(forbidden).intersection(self.forbidden)])
        # search for centromere:
        if self._search_centromere:
            self._find_centromere(xpr)
        # add centromere as forbidden region:
        if self._centromere:
            for pos in xrange(int(self._centromere[0]),
                              int(self._centromere[1])):
                self.forbidden[pos] = 'Centromere'
        if not resized:
            self.__update_size(xpr)


    def get_experiment(self, name):
        """
        Fetch an Experiment according to its name.
        This can also be done directly with Chromosome.experiments[name].

        :param name: name of the experiment to select
        :returns: :class:`pytadbit.Experiment`
        """
        for exp in self.experiments:
            if exp.name == name:
                return exp
        raise Exception(('ERROR: experiment ' +
                         '%s not found\n') % (name))


    def save_chromosome(self, out_f, fast=True, divide=True, force=False):
        """
        Save a Chromosome object to a file (it uses :py:func:`pickle.load` from
        the :py:mod:`cPickle`). Once saved, the object can be loaded with
        :func:`load_chromosome`.

        :param out_f: path to the file where to store the :py:mod:`cPickle`
           object
        :param True fast: if True, skip Hi-C data and weights
        :param True divide: if True writes two pickles, one with what would
           result by using the fast option, and the second with the Hi-C and 
           weights data. The second file name will be extended by '_hic' (ie:
           with out_f='chromosome12.pik' we would obtain chromosome12.pik and
           chromosome12.pik_hic). When loaded :func:`load_chromosome` will
           automatically search for both files
        :param False force: overwrite the existing file

        """
        while exists(out_f) and not force:
            out_f += '_'
        dico = {'experiments': {},
                'experiment_order': [xpr.name for xpr in self.experiments]}
        if divide:
            dicp = {}
        for xpr in self.experiments:
            dico['experiments'][xpr.name] = {
                'size'      : xpr.size,
                'cond'      : xpr.conditions,
                'tads'      : xpr.tads,
                'resolution': xpr.resolution,
                'hi-c'      : None,
                'wght'      : None,
                'iden'      : xpr.identifier,
                'cell'      : xpr.cell_type,
                'expt'      : xpr.exp_type,
                'enzy'      : xpr.enzyme,
                'desc'      : xpr.description,
                'zero'      : xpr._zeros
                }
            if fast:
                continue
            if divide:
                dicp[xpr.name] = {
                    'wght': xpr.norm,
                    'hi-c': xpr.hic_data}
                dico['experiments'][xpr.name]['wght'] = None
                dico['experiments'][xpr.name]['hi-c'] = None
            else:
                dico['experiments'][xpr.name]['wght'] = xpr.norm
                dico['experiments'][xpr.name]['hi-c'] = xpr.hic_data
        dico['name']         = self.name
        dico['size']         = self.size
        dico['r_size']       = self.r_size
        dico['max_tad_size'] = self.max_tad_size
        dico['forbidden']    = self.forbidden
        dico['_centromere']  = self._centromere
        dico['species']      = self.species
        dico['assembly']     = self.assembly
        dico['description']  = self.description
        out = open(out_f, 'w')
        dump(dico, out)
        out.close()
        if not fast:
            out = open(out_f + '_hic', 'w')
            dump(dicp, out)
            out.close()



    def add_experiment(self, name, resolution=None, tad_def=None,
                       hic_data=None, norm_data=None, replace=False,
                       parser=None, conditions=None, **kwargs):
        """
        Add a Hi-C experiment to Chromosome
        
        :param name: name of the experiment or of the Experiment object
        :param resolution: resolution of the experiment (needed if name is not
           an Experiment object)
        :param None hic_data: whether a file or a list of lists corresponding to
           the Hi-C data
        :param None tad_def: a file or a dict with precomputed TADs for this
           experiment
        :param False replace: overwrite the experiments loaded under the same
           name
        :param None parser: a parser function that returns a tuple of lists 
           representing the data matrix and the length of a row/column. With 
           a file example.tsv containing:

           ::
           
             chrT_001	chrT_002	chrT_003	chrT_004
             chrT_001	629	164	88	105
             chrT_002	164	612	175	110
             chrT_003	88	175	437	100
             chrT_004	105	110	100	278
           
           the output of parser('example.tsv') would be:
           ``[([629, 164, 88, 105, 164, 612, 175, 110, 88, 175, 437, 100, 105,
           110, 100, 278]), 4]``
        
        """
        if not name:
            name = ''.join([letters[int(random() * len(letters))] \
                            for _ in xrange(5)])
            stderr.write('WARNING: No name provided, random name ' +
                         'generated: %s\n' % (name))
        if name in self.experiments:
            if 'hi-c' in self.get_experiment(name) and not replace:
                stderr.write(
                    '''WARNING: Hi-C data already loaded under the name: %s.
                    This experiment will be kept under %s.\n''' % (name,
                                                                   name + '_'))
                name += '_'
        if isinstance(name, Experiment):
            self.experiments.append(name)
        elif resolution:
            self.experiments.append(Experiment(
                name, resolution, hic_data=hic_data, norm_data=norm_data,
                tad_def=tad_def, parser=parser, conditions=conditions,
                **kwargs))
        else:
            raise Exception('resolution param is needed\n')


    def __update_size(self, xpr):
        """
        Update the chromosome size and relative size after loading new Hi-C
        experiments, unless the Chromosome size was defined by hand.
        
        """
        if not self._given_size:
            self.size = max(xpr.tads[max(xpr.tads)]['end'] * xpr.resolution,
                            self.size)
            self.size   = ChromosomeSize(self.size)
        self._get_forbidden_region(xpr, resized=True)
            
        self.r_size = self.size - len(self.forbidden) * xpr.resolution
        self.r_size = RelativeChromosomeSize(self.size)


    def tad_density_plot(self, name, axe=None, focus=None, extras=None,
                         normalized=True, savefig=None, shape='ellipse'):
        """
        Draw an summary of the TAD found in a given experiment and their density
        in terms of relative Hi-C interaction count.
        
        :param name: name of the experiment to visualize
        :param None focus: can pass a tuple (bin_start, bin_stop) to display the
           alignment between these genomic bins
        :param None extras: list of coordinates (genomic bin) where to draw a
           red cross
        :param None ymax: limit the y axis up to a given value
        :param ('grey', ): successive colors for alignment
        :param True normalized: normalized Hi-C count are plotted instead of raw
           data.
        :param 'ellipse' shape: which kind of shape to use as schematic
           representation of TADs. Implemented: 'ellipse', 'rectangle',
           'triangle'
        :param None savefig: path to a file where to save the image generated;
           if None, the image will be shown using matplotlib GUI (the extension
           of the file name will determine the desired format).
        """
        if not self.experiments[name].tads:
            raise Exception("TAD borders not found\n")
        _tad_density_plot(self.experiments[name], axe=axe, focus=focus,
                          extras=extras, normalized=normalized,
                          savefig=savefig, shape=shape)
        

    def get_tad_hic(self, tad, x_name, normed=True, matrix_num=0):
        """
        Retrieve the Hi-C data matrix corresponding to a given TAD.
        
        :param tad: a given TAD (:py:class:`dict`)
        :param x_name: name of the experiment
        :param True normed: if True, normalize the Hi-C data
        
        :returns: Hi-C data matrix for the given TAD
        """
        beg, end = int(tad['start']), int(tad['end'])
        xpr = self.get_experiment(x_name)
        size = xpr.size
        matrix = [[0 for _ in xrange(beg, end)]\
                  for _ in xrange(beg, end)]
        for i, tadi in enumerate(xrange(beg, end)):
            tadi = tadi * size
            for j, tadj in enumerate(xrange(beg, end)):
                if normed:
                    matrix[j][i] = xpr.hic_data[matrix_num][tadi + tadj]
                else:
                    matrix[j][i] = xpr.norm[0][tadi + tadj]
        return matrix


    def iter_tads(self, x_name, normed=True):
        """
        Iterate over the TADs corresponding to a given experiment.

        :param x_name: name of the experiment
        :param True normed: normalize Hi-C data returned

        :yields: Hi-C data corresponding to each TAD
        """
        if not self.get_experiment(x_name).hic_data:
            raise Exception('No Hi-c data for %s experiment\n' % (x_name))
        for name, ref in self.get_experiment(x_name).tads.iteritems():
            yield name, self.get_tad_hic(ref, x_name, normed=normed)


    def set_max_tad_size(self, value):
        """
        Change the maximum size allowed for TADs. It also applies to the
        computed experiments.

        :param value: an int value (default is 5000000)
        """
        self.max_tad_size = value
        for xpr in self.experiments:
            for tad in xpr.tads:
                xpr.tads[tad]['brk'] = xpr.tads[tad]['end']
                if ((xpr.tads[tad]['end'] - xpr.tads[tad]['start'])
                    * xpr.resolution) > self.max_tad_size:
                    xpr.tads[tad]['score'] = -abs(xpr.tads[tad]['score'])


    def _find_centromere(self, xpr):
        """
        Search for the centromere in a chromosome, assuming that
        :class:`Chromosome` corresponds to a real chromosome.
        Add a boundary to all the experiments where the centromere is.
         * A centromere is defined as the largest area where the rows/columns
           of the Hi-C matrix are empty.
        """
        beg = end = 0
        size = xpr.size
        try:
            hic = xpr.hic_data[0]
        except TypeError:
            return
        # search for largest empty region of the chromosome
        best = (0, 0, 0)
        pos = 0
        for pos, raw in enumerate(xrange(0, size * size, size)):
            if sum([hic[i] for i in xrange(raw, raw + size)]) == 0 and not beg:
                beg = float(pos)
            if sum([hic[i] for i in xrange(raw, raw + size)]) != 0 and beg:
                end = float(pos)
                if (end - beg) > best[0]:
                    best = ((end - beg), beg, end)
                beg = end = 0
        # this is for weared cases where centromere is at the end of Hi-C data
        if beg and not end:
            end = float(pos)
            if (end - beg) > best[0]:
                best = ((end - beg), beg, end)
        beg, end = best[1:]
        if not beg or not end:
            return
        tads = xpr.tads
        # if we already have a centromere defined, check if it can be reduced
        if self._centromere:
            if beg > self._centromere[0]:
                # readjust TADs that have been split around the centromere
                for tad in tads:
                    if tads[tad]['end'] == self._centromere[0]:
                        tads[tad]['end'] = beg
                self._centromere[0] = beg
            if end < self._centromere[1]:
                # readjust TADs that have been split around the centromere
                for tad in tads:
                    if tads[tad]['start'] == self._centromere[1]:
                        tads[tad]['start'] = end
                self._centromere[1] = end
        else:
            self._centromere = [beg, end]
        # split TADs overlapping  with the centromere
        if [True for t in tads.values() \
            if t['start'] < beg < t['end'] \
            and t['start'] < end < t['end']]:
            tad  = len(tads) + 1
            plus = 0
            while tad + plus > 1:
                start = tads[tad - 1 + plus]['start']
                final = tads[tad - 1 + plus]['end']
                # centromere found?
                if start < beg < final and start < end < final:
                    tads[tad] = copy(tads[tad - 1])
                    tads[tad]['start'] = end
                    tads[tad]['score'] = abs(tads[tad]['score'])
                    if (tads[tad]['end'] - tads[tad]['start']) \
                           * xpr.resolution > self.max_tad_size:
                        xpr.tads[tad]['score'] = -abs(xpr.tads[tad]['score'])
                    tads[tad]['brk'] = tads[tad]['end']
                    tad -= 1
                    tads[tad] = copy(tads[tad])
                    tads[tad]['score'] = abs(tads[tad]['score'])
                    tads[tad]['end'] = beg
                    if (tads[tad]['end'] - tads[tad]['start']) \
                           * xpr.resolution > self.max_tad_size:
                        xpr.tads[tad]['score'] = -abs(xpr.tads[tad]['score'])
                    tads[tad]['brk'] = tads[tad]['end']
                    plus = 1
                else:
                    tads[tad] = copy(tads[tad - 1 + plus])
                tad -= 1
        # if tad includes centromere but starts in the same point
        elif [True for t in tads.values() \
              if t['start'] == beg and end < t['end']]:
            tad  = len(tads) + 1
            while tad > 1:
                start = tads[tad - 1]['start']
                final = tads[tad - 1]['end']
                # centromere found?
                if start == beg:
                    tads[tad] = copy(tads[tad - 1])
                    tads[tad]['start'] = end
                    tads[tad]['score'] = abs(tads[tad]['score'])
                    if (tads[tad]['end'] - tads[tad]['start']) \
                           * xpr.resolution > self.max_tad_size:
                        xpr.tads[tad]['score'] = -abs(xpr.tads[tad]['score'])
                else:
                    tads[tad] = copy(tads[tad - 1])
                tad -= 1
        # if tad includes centromere but ends in the same point
        elif [True for t in tads.values() \
              if t['end'] == end and beg > t['start']]:
            tad  = len(tads) + 1
            plus = 0
            while tad + plus > 1:
                start = tads[tad - 1 + plus]['start']
                final = tads[tad - 1 + plus]['end']
                # centromere found?
                if final == end:
                    tads[tad] = copy(tads[tad - 1])
                    tads[tad]['start'] = beg
                    tads[tad]['score'] = abs(tads[tad]['score'])
                    if (tads[tad]['end'] - tads[tad]['start']) \
                           * xpr.resolution > self.max_tad_size:
                        xpr.tads[tad]['score'] = -abs(xpr.tads[tad]['score'])
                    tads[tad]['brk'] = tads[tad]['end']
                    tad -= 1
                    tads[tad] = copy(tads[tad])
                    tads[tad]['score'] = abs(tads[tad]['score'])
                    tads[tad]['end'] = beg
                    if (tads[tad]['end'] - tads[tad]['start']) \
                           * xpr.resolution > self.max_tad_size:
                        xpr.tads[tad]['score'] = -abs(xpr.tads[tad]['score'])
                    tads[tad]['brk'] = tads[tad]['end']
                    plus = 1
                else:
                    tads[tad] = copy(tads[tad - 1 + plus])
                tad -= 1



class ExperimentList(list):
    """
    Inherited from python built in :py:func:`list`, modified for tadbit
    :class:`pytadbit.Experiment`.

    Mainly, `getitem`, `setitem`, and `append` were modified in order to
    be able to search for experiments by index or by name, and to add
    experiments simply using Chromosome.experiments.append(Experiment).

    The whole ExperimentList object is linked to a Chromosome instance
    (:class:`pytadbit.Chromosome`).

    """
    def __init__(self, thing, crm):
        super(ExperimentList, self).__init__(thing)
        self.crm = crm


    def __getitem__(self, i):
        try:
            return super(ExperimentList, self).__getitem__(i)
        except TypeError:
            for nam in self:
                if nam.name == i:
                    return nam
            raise KeyError('Experiment %s not found\n' % (i))


    def __setitem__(self, i, exp):
        try:
            super(ExperimentList, self).__setitem__(i, exp)
            exp.crm = self.crm
            self.crm._get_forbidden_region(exp)
        except TypeError:
            for j, nam in enumerate(self):
                if nam.name == i:
                    exp.crm = self.crm
                    self[j] = exp
                    self.crm._get_forbidden_region(exp)
                    break
            else:
                exp.crm = self.crm
                self.append(exp)
                self.crm._get_forbidden_region(exp)


    def __delitem__(self, i):
        try:
            super(ExperimentList, self).__delitem__(i)
        except TypeError:
            for j, nam in enumerate(self):
                if nam.name == i:
                    exp = self.pop(j)
                    del(exp)
                    break
            else:
                raise KeyError('Experiment %s not found\n' % (i))


    def append(self, exp):
        if exp.name in [e.name for e in self]:
            self[exp.name] = exp
            self.crm._get_forbidden_region(exp)
        else:
            super(ExperimentList, self).append(exp)
            self.crm._get_forbidden_region(exp)
            exp.crm = self.crm


class AlignmentDict(dict):
    """
    :py:func:`dict` of :class:`pytadbit.Alignment`
    
    Modified getitem, setitem, and append in order to be able to search
    alignments by index or by name.

    linked to a :class:`pytadbit.Chromosome`
    """

    def __getitem__(self, nam):
        try:
            return super(AlignmentDict, self).__getitem__(tuple(sorted(nam)))
        except KeyError:
            for i, key in enumerate(self):
                if nam == i:
                    return self[key]
            raise TypeError('Alignment %s not found\n' % i)


class ChromosomeSize(int):
    """
    Chromosome size in base pairs
    """
    def __init__(self, thing):
        super(ChromosomeSize, self).__init__(thing)


class RelativeChromosomeSize(int):
    """
    Relative Chromosome size in base pairs. Equal to Chromosome size minus
    forbidden regions (eg: the centromere)

    Only used for TAD alignment randomization.
    """
    def __init__(self, thing):
        super(RelativeChromosomeSize, self).__init__(thing)
