'''
Class for a bipartite network
'''
from pandas.core.indexes.base import InvalidIndexError
from tqdm.auto import tqdm
import numpy as np
# from numpy_groupies.aggregate_numpy import aggregate
import pandas as pd
from pandas import DataFrame, Int64Dtype
# from scipy.sparse.csgraph import connected_components
import warnings
import bipartitepandas as bpd
from bipartitepandas import col_order, update_dict, to_list, logger_init, col_dict_optional_cols, aggregate_transform, ParamsDict
import igraph as ig

def recollapse_loop(force=False):
    '''
    Decorator function that accounts for issues with selecting ids under particular restrictions for collapsed data. In particular, looking at a restricted set of observations can require recollapsing data, which can they change which observations meet the given restrictions. This function loops until stability is achieved.

    Arguments:
        force (bool): if True, force loop for non-collapsed data
    '''
    def recollapse_loop_inner(func):
        def recollapse_loop_inner_inner(*args, **kwargs):
            # Do function
            self = args[0]
            frame = func(*args, **kwargs)

            if force or isinstance(self, (bpd.BipartiteLongCollapsed, bpd.BipartiteEventStudyCollapsed)):
                kwargs['copy'] = False
                if len(frame) != len(self):
                    # If the frame changes, we have to re-loop until stability
                    frame_prev = frame
                    frame = func(frame_prev, *args[1:], **kwargs)
                    while len(frame) != len(frame_prev):
                        frame_prev = frame
                        frame = func(frame_prev, *args[1:], **kwargs)

            return frame
        return recollapse_loop_inner_inner
    return recollapse_loop_inner

# Define default parameter dictionary
clean_params_default = ParamsDict({
    'connectedness': ('connected', 'set', ['connected', 'leave_one_observation_out', 'leave_one_firm_out', None],
        '''
            (default='connected') When computing largest connected set of firms: if 'connected', keep observations in the largest connected set of firms; if 'leave_one_observation_out', keep observations in the largest leave-one-observation-out connected set; if 'leave_one_firm_out', keep observations in the largest leave-one-firm-out connected set; if None, keep all observations.
        '''),
    'component_size_variable': ('firms', 'set', ['len', 'length', 'firms', 'workers', 'stayers', 'movers'],
        '''
        (default='firms') How to determine largest connected component. Options are 'len'/'length' (length of frame), 'firms' (number of unique firms), 'workers' (number of unique workers), 'stayers' (number of unique stayers), and 'movers' (number of unique movers).
        '''),
    'i_t_how': ('max', 'set', ['max', 'sum', 'mean'],
        '''
            (default='max') When dropping i-t duplicates: if 'max', keep max paying job; if 'sum', sum over duplicate worker-firm-year observations, then take the highest paying worker-firm sum; if 'mean', average over duplicate worker-firm-year observations, then take the highest paying worker-firm average. Note that if multiple time and/or firm columns are included (as in event study format), then data is converted to long, cleaned, then reconverted to its original format.
        '''),
    'drop_multiples': (False, 'type', bool,
        '''
            (default=False) If True, rather than collapsing over spells, drop any spells with multiple observations (this is for computational efficiency when re-collapsing data for biconnected components).
        '''),
    'is_sorted': (False, 'type', bool,
        '''
            (default=False) If False, dataframe will be sorted by i (and t, if included). Set to True if already sorted.
        '''),
    'force': (True, 'type', bool,
        '''
            (default=True) If True, force all cleaning methods to run; much faster if set to False.
        '''),
    'copy': (True, 'type', bool,
        '''
            (default=True) If False, avoid copying data when possible.
        ''')
})

def clean_params(update_dict={}):
    '''
    Dictionary of default clean_params.

    Arguments:
        update_dict (dict): user parameter values

    Returns:
        (ParamsDict) dictionary of clean_params
    '''
    new_dict = clean_params_default.copy()
    for k, v in update_dict.items():
        new_dict[k] = v
    return new_dict

class BipartiteBase(DataFrame):
    '''
    Base class for BipartitePandas, where BipartitePandas gives a bipartite network of firms and workers. Contains generalized methods. Inherits from DataFrame.

    Arguments:
        *args: arguments for Pandas DataFrame
        columns_req (list): required columns (only put general column names for joint columns, e.g. put 'j' instead of 'j1', 'j2'; then put the joint columns in reference_dict)
        columns_opt (list): optional columns (only put general column names for joint columns, e.g. put 'g' instead of 'g1', 'g2'; then put the joint columns in reference_dict)
        columns_contig (dictionary): columns requiring contiguous ids linked to boolean of whether those ids are contiguous, or None if column(s) not included, e.g. {'i': False, 'j': False, 'g': None} (only put general column names for joint columns)
        reference_dict (dict): clarify which columns are associated with a general column name, e.g. {'i': 'i', 'j': ['j1', 'j2']}
        col_dtype_dict (dict): link column to datatype
        col_dict (dict or None): make data columns readable. Keep None if column names already correct
        include_id_reference_dict (bool): if True, create dictionary of Pandas dataframes linking original id values to contiguous id values
        log (bool): if True, will create log file(s)
        **kwargs: keyword arguments for Pandas DataFrame
    '''
    # Attributes, required for Pandas inheritance
    _metadata = ['col_dict', 'reference_dict', 'id_reference_dict', 'col_dtype_dict', 'columns_req', 'columns_opt', 'columns_contig', 'default_cluster', 'dtype_dict', 'default_clean', 'connectedness', 'no_na', 'no_duplicates', 'i_t_unique', '_log_on_indicator', '_level_fn_dict']

    def __init__(self, *args, columns_req=[], columns_opt=[], columns_contig=[], reference_dict={}, col_dtype_dict={}, col_dict=None, include_id_reference_dict=False, log=True, **kwargs):
        # Initialize DataFrame
        super().__init__(*args, **kwargs)

        # Start logger
        logger_init(self)
        # Option to turn on/off logger
        self._log_on_indicator = log
        # self.log('initializing BipartiteBase object', level='info')

        if len(args) > 0 and isinstance(args[0], BipartiteBase):
            # Note that isinstance works for subclasses
            self._set_attributes(args[0], include_id_reference_dict)
        else:
            self.columns_req = ['i', 'j', 'y'] + columns_req
            self.columns_opt = ['g', 'm'] + columns_opt
            self.columns_contig = update_dict({'i': False, 'j': False, 'g': None}, columns_contig)
            self.reference_dict = update_dict({'i': 'i', 'm': 'm'}, reference_dict)
            self._reset_id_reference_dict(include_id_reference_dict) # Link original id values to contiguous id values
            self.col_dtype_dict = update_dict({'i': 'int', 'j': 'int', 'y': 'float', 't': 'int', 'g': 'int', 'm': 'int'}, col_dtype_dict)
            default_col_dict = {}
            for col in to_list(self.columns_req):
                for subcol in to_list(self.reference_dict[col]):
                    default_col_dict[subcol] = subcol
            for col in to_list(self.columns_opt):
                for subcol in to_list(self.reference_dict[col]):
                    default_col_dict[subcol] = None

            # Create self.col_dict
            self.col_dict = col_dict_optional_cols(default_col_dict, col_dict, self.columns, optional_cols=[self.reference_dict[col] for col in self.columns_opt])

            # Set attributes
            self._reset_attributes()

        # Dictionary of logger functions based on level
        self._level_fn_dict = {
            'debug': self.logger.debug,
            'info': self.logger.info,
            'warning': self.logger.warning,
            'error': self.logger.error,
            'critical': self.logger.critical
        }

        self.dtype_dict = {
            'int': ['int', 'int8', 'int16', 'int32', 'int64', 'Int64'],
            'float': ['float', 'float8', 'float16', 'float32', 'float64', 'float128', 'int', 'int8', 'int16', 'int32', 'int64', 'Int64'],
            'str': 'str'
        }

        # self.log('BipartiteBase object initialized', level='info')

    @property
    def _constructor(self):
        '''
        For inheritance from Pandas.
        '''
        return BipartiteBase

    def copy(self):
        '''
        Return copy of self.

        Returns:
            bdf_copy (BipartiteBase): copy of instance
        '''
        df_copy = DataFrame(self, copy=True)
        # Set logging on/off depending on current selection
        bdf_copy = self._constructor(df_copy, log=self._log_on_indicator)
        # This copies attribute dictionaries, default copy does not
        bdf_copy._set_attributes(self)

        return bdf_copy

    def log_on(self, on=True):
        '''
        Toggle logger on or off.

        Arguments:
            on (bool): if True, turn logger on; if False, turn logger off
        '''
        self._log_on_indicator = on

    def log(self, message, level='info'):
        '''
        Log a message at the specified level.

        Arguments:
            message (str): message to log
            level (str): logger level. Options, in increasing severity, are 'debug', 'info', 'warning', 'error', and 'critical'.
        '''
        if self._log_on_indicator:
            # Log message
            self._level_fn_dict[level](message)

    def summary(self):
        '''
        Print summary statistics. This uses class attributes. To run a diagnostic to verify these values, run `.diagnostic()`.
        '''
        ret_str = ''
        y = self.loc[:, self.reference_dict['y']].to_numpy()
        mean_wage = np.mean(y)
        median_wage = np.median(y)
        max_wage = np.max(y)
        min_wage = np.min(y)
        var_wage = np.var(y)
        ret_str += 'format: {}\n'.format(type(self).__name__)
        ret_str += 'number of workers: {}\n'.format(self.n_workers())
        ret_str += 'number of firms: {}\n'.format(self.n_firms())
        ret_str += 'number of observations: {}\n'.format(len(self))
        ret_str += 'mean wage: {}\n'.format(mean_wage)
        ret_str += 'median wage: {}\n'.format(median_wage)
        ret_str += 'min wage: {}\n'.format(min_wage)
        ret_str += 'max wage: {}\n'.format(max_wage)
        ret_str += 'var(wage): {}\n'.format(var_wage)
        ret_str += 'no NaN values: {}\n'.format(self.no_na)
        ret_str += 'no duplicates: {}\n'.format(self.no_duplicates)
        ret_str += 'i-t (worker-year) observations unique (None if t column(s) not included): {}\n'.format(self.i_t_unique)
        for contig_col, is_contig in self.columns_contig.items():
            ret_str += 'contiguous {} ids (None if not included): {}\n'.format(contig_col, is_contig)
        ret_str += 'connectedness (None if ignoring connectedness): {}'.format(self.connectedness)

        print(ret_str)

    def diagnostic(self):
        '''
        Run diagnostic and print diagnostic report.
        '''
        ret_str = '----- General Diagnostic -----\n'
        ##### Sorted by i (and t, if included) #####
        sort_order = ['i']
        if self._col_included('t'):
            # If t column
            sort_order.append(to_list(self.reference_dict['t'])[0])
        is_sorted = (self.loc[:, sort_order] == self.loc[:, sort_order].sort_values(sort_order)).to_numpy().all()

        ret_str += 'sorted by i (and t, if included): {}\n'.format(is_sorted)

        ##### No NaN values #####
        # Source: https://stackoverflow.com/a/29530601/17333120
        no_na = (not self.isnull().to_numpy().any())

        ret_str += 'no NaN values: {}\n'.format(no_na)

        ##### No duplicates #####
        # https://stackoverflow.com/a/50243108/17333120
        no_duplicates = (not self.duplicated().any())

        ret_str += 'no duplicates: {}\n'.format(no_duplicates)

        ##### i-t unique #####
        no_i_t_duplicates = (not self.duplicated(subset=sort_order).any())

        ret_str += 'i-t (worker-year) observations unique (if t column(s) not included, then i observations unique): {}\n'.format(no_i_t_duplicates)

        ##### Contiguous ids #####
        for contig_col in self.columns_contig.keys():
            if self._col_included(contig_col):
                contig_ids = self.unique_ids(contig_col)
                is_contig = (len(contig_ids) == (max(contig_ids) + 1))
                ret_str += 'contiguous {} ids (None if not included): {}\n'.format(contig_col, is_contig)
            else:
                ret_str += 'contiguous {} ids (None if not included): {}\n'.format(contig_col, None)

        ##### Connectedness #####
        is_connected_dict = {
            None: lambda : None,
            'connected': lambda : self._construct_graph(self.connectedness).is_connected(),
            'leave_one_observation_out': lambda: (len(self) == len(self._conset(connectedness=self.connectedness))),
            'leave_one_firm_out': lambda: (len(self) == len(self._conset(connectedness=self.connectedness)))
        }
        is_connected = is_connected_dict[self.connectedness]()

        if is_connected or (is_connected is None):
            ret_str += 'frame is (None if ignoring connectedness): {}\n'.format(self.connectedness)
        else:
            ret_str += 'frame failed connectedness: {}\n'.format(self.connectedness)

        if self._col_included('m'):
            ##### m column #####
            m_correct = (self.loc[:, 'm'] == self.gen_m(force=True).loc[:, 'm']).to_numpy().all()

            ret_str += "'m' column correct (None if not included): {}\n".format(m_correct)
        else:
            ret_str += "'m' column correct (None if not included): {}".format(None)

        print(ret_str)

    def unique_ids(self, id_col):
        '''
        Unique ids in column.

        Arguments:
            id_col (str): column to check ids ('i', 'j', or 'g'). Use general column names for joint columns, e.g. put 'j' instead of 'j1', 'j2'

        Returns:
            (NumPy Array): unique ids
        '''
        id_lst = []
        for id_subcol in to_list(self.reference_dict[id_col]):
            id_lst += list(self.loc[:, id_subcol].unique())
        return np.array(list(set(id_lst)))

    def n_unique_ids(self, id_col):
        '''
        Number of unique ids in column.

        Arguments:
            id_col (str): column to check ids ('i', 'j', or 'g'). Use general column names for joint columns, e.g. put 'j' instead of 'j1', 'j2'

        Returns:
            (int): number of unique ids
        '''
        return len(self.unique_ids(id_col))

    def n_workers(self):
        '''
        Get the number of unique workers.

        Returns:
            (int): number of unique workers
        '''
        return self.loc[:, 'i'].nunique()

    def n_firms(self):
        '''
        Get the number of unique firms.

        Returns:
            (int): number of unique firms
        '''
        return self.n_unique_ids('j')

    def n_clusters(self):
        '''
        Get the number of unique clusters.

        Returns:
            (int or None): number of unique clusters, None if not clustered
        '''
        if not self._col_included('g'): # If cluster column not in dataframe
            return None
        return self.n_unique_ids('g')

    def original_ids(self, copy=True):
        '''
        Return self merged with original column ids.

        Arguments:
            copy (bool): if False, avoid copy

        Returns:
            (BipartiteBase or None): copy of self merged with original column ids, or None if id_reference_dict is empty
        '''
        frame = pd.DataFrame(self, copy=copy)
        if self.id_reference_dict:
            for id_col, reference_df in self.id_reference_dict.items():
                if len(reference_df) > 0: # Make sure non-empty
                    for id_subcol in to_list(self.reference_dict[id_col]):
                        try:
                            frame = frame.merge(reference_df.loc[:, ['original_ids', 'adjusted_ids_' + str(len(reference_df.columns) - 1)]].rename({'original_ids': 'original_' + id_subcol, 'adjusted_ids_' + str(len(reference_df.columns) - 1): id_subcol}, axis=1), how='left', on=id_subcol)
                        except TypeError: # Int64 error with NaNs
                            frame.loc[:, id_col] = frame.loc[:, id_col].astype('Int64', copy=False)
                            frame = frame.merge(reference_df.loc[:, ['original_ids', 'adjusted_ids_' + str(len(reference_df.columns) - 1)]].rename({'original_ids': 'original_' + id_subcol, 'adjusted_ids_' + str(len(reference_df.columns) - 1): id_subcol}, axis=1), how='left', on=id_subcol)
                # else:
                #     # If no changes, just make original_id be the same as the current id
                #     for id_subcol in to_list(self.reference_dict[id_col]):
                #         frame['original_' + id_subcol] = frame[id_subcol]
            return frame
        else:
            warnings.warn('id_reference_dict is empty. Either your id columns are already correct, or you did not specify `include_id_reference_dict=True` when initializing your BipartitePandas object')

            return None

    def _set_attributes(self, frame, no_dict=False, include_id_reference_dict=False):
        '''
        Set class attributes to equal those of another BipartitePandas object.

        Arguments:
            frame (BipartitePandas): BipartitePandas object whose attributes to use
            no_dict (bool): if True, only set booleans, no dictionaries
            include_id_reference_dict (bool): if True, create dictionary of Pandas dataframes linking original id values to contiguous id values
        '''
        # Dictionaries
        if not no_dict:
            self.columns_req = frame.columns_req.copy()
            self.columns_opt = frame.columns_opt.copy()
            self.reference_dict = frame.reference_dict.copy()
            self.col_dtype_dict = frame.col_dtype_dict.copy()
            self.col_dict = frame.col_dict.copy()
        self.columns_contig = frame.columns_contig.copy() # Required, even if no_dict
        if frame.id_reference_dict:
            self.id_reference_dict = {}
            # Must do a deep copy
            for id_col, reference_df in frame.id_reference_dict.items():
                self.id_reference_dict[id_col] = reference_df.copy()
        else:
            # This is if the original dataframe DIDN'T have an id_reference_dict (but the new dataframe may or may not)
            self._reset_id_reference_dict(include_id_reference_dict)
        # # Logger
        # self.logger = frame.logger
        # Booleans
        self.connectedness = frame.connectedness # If False, not connected; if 'connected', all observations are in the largest connected set of firms; if 'leave_one_observation_out', observations are in the largest leave-one-observation-out connected set; if 'leave_one_firm_out', observations are in the largest leave-one-firm-out connected set; if None, connectedness ignored
        self.no_na = frame.no_na # If True, no NaN observations in the data
        self.no_duplicates = frame.no_duplicates # If True, no duplicate rows in the data
        self.i_t_unique = frame.i_t_unique # If True, each worker has at most one observation per period

    def _reset_attributes(self, columns_contig=True, connected=True, no_na=True, no_duplicates=True, i_t_unique=True):
        '''
        Reset class attributes conditions to be False/None.

        Arguments:
            columns_contig (bool): if True, reset self.columns_contig
            connected (bool): if True, reset self.connectedness
            no_na (bool): if True, reset self.no_na
            no_duplicates (bool): if True, reset self.no_duplicates
            i_t_unique (bool): if True, reset self.i_t_unique

        Returns:
            self (BipartiteBase): self with reset class attributes
        '''
        if columns_contig:
            for contig_col in self.columns_contig.keys():
                if self._col_included(contig_col):
                    self.columns_contig[contig_col] = False
                else:
                    self.columns_contig[contig_col] = None
        if connected:
            self.connectedness = None # If False, not connected; if 'connected', all observations are in the largest connected set of firms; if 'leave_one_observation_out', observations are in the largest leave-one-observation-out connected set; if 'leave_one_firm_out', observations are in the largest leave-one-firm-out connected set; if None, connectedness ignored
        if no_na:
            self.no_na = False # If True, no NaN observations in the data
        if no_duplicates:
            self.no_duplicates = False # If True, no duplicate rows in the data
        if i_t_unique:
            self.i_t_unique = None # If True, each worker has at most one observation per period; if None, t column not included (set to False later in method if t column included)

            # Verify whether period included
            if self._col_included('t'):
                self.i_t_unique = False

        # logger_init(self)

        return self

    def _reset_id_reference_dict(self, include=False):
        '''
        Reset id_reference_dict.

        Arguments:
            include (bool): if True, id_reference_dict will track changes in ids

        Returns:
            self (BipartiteBase): self with reset id_reference_dict
        '''
        if include:
            self.id_reference_dict = {id_col: pd.DataFrame() for id_col in self.reference_dict.keys()}
        else:
            self.id_reference_dict = {}

        return self

    def _col_included(self, col):
        '''
        Check whether a column from the pre-established required/optional lists is included.

        Arguments:
            col (str): column to check. Use general column names for joint columns, e.g. put 'j' instead of 'j1', 'j2'

        Returns:
            (bool): if True, column is included
        '''
        if col in self.columns_req + self.columns_opt:
            for subcol in to_list(self.reference_dict[col]):
                if self.col_dict[subcol] is None:
                    return False
            return True
        return False

    def _included_cols(self, flat=False):
        '''
        Get all columns included from the pre-established required/optional lists.
        
        Arguments:
            flat (bool): if False, uses general column names for joint columns, e.g. returns 'j' instead of 'j1', 'j2'.

        Returns:
            all_cols (list): included columns
        '''
        all_cols = []
        for col in self.columns_req + self.columns_opt:
            include = True
            for subcol in to_list(self.reference_dict[col]):
                if self.col_dict[subcol] is None:
                    include = False
                    break
            if include:
                if flat:
                    all_cols += to_list(self.reference_dict[col])
                else:
                    all_cols.append(col)
        return all_cols

    def drop(self, indices, axis=0, inplace=False, allow_required=False):
        '''
        Drop indices along axis.

        Arguments:
            indices (int or str, optionally as a list): row(s) or column(s) to drop. For columns, use general column names for joint columns, e.g. put 'g' instead of 'g1', 'g2'. Only optional columns may be dropped
            axis (int): 0 to drop rows, 1 to drop columns
            inplace (bool): if True, modify in-place
            allow_required (bool): if True, allow to drop required columns

        Returns:
            frame (BipartiteBase): BipartiteBase with dropped indices
        '''
        frame = self

        if axis == 1:
            for col in to_list(indices):
                if col in frame.columns or col in frame.columns_req or col in frame.columns_opt:
                    if col in frame.columns_opt: # If column optional
                        for subcol in to_list(frame.reference_dict[col]):
                            if inplace:
                                DataFrame.drop(frame, subcol, axis=1, inplace=True)
                            else:
                                frame = DataFrame.drop(frame, subcol, axis=1, inplace=False)
                            frame.col_dict[subcol] = None
                        if col in frame.columns_contig.keys(): # If column contiguous
                            frame.columns_contig[col] = None
                            if frame.id_reference_dict: # If id_reference_dict has been initialized
                                frame.id_reference_dict[col] = pd.DataFrame()
                    elif col not in frame._included_cols() and col not in frame._included_cols(flat=True): # If column is not pre-established
                        if inplace:
                            DataFrame.drop(frame, col, axis=1, inplace=True)
                        else:
                            frame = DataFrame.drop(frame, col, axis=1, inplace=False)
                    else:
                        if not allow_required:
                            warnings.warn("{} is either (a) a required column and cannot be dropped or (b) a subcolumn that can be dropped, but only by specifying the general column name (e.g. use 'g' instead of 'g1' or 'g2')".format(col))
                        else:
                            if inplace:
                                DataFrame.drop(frame, col, axis=1, inplace=True)
                            else:
                                frame = DataFrame.drop(frame, col, axis=1, inplace=False)
                else:
                    warnings.warn('{} is not in data columns'.format(col))
        elif axis == 0:
            if inplace:
                DataFrame.drop(frame, indices, axis=0, inplace=True)
            else:
                frame = DataFrame.drop(frame, indices, axis=0, inplace=False)
            frame._reset_attributes()
            # frame.clean_data({'connectedness': frame.connectedness})

        return frame

    def rename(self, rename_dict, inplace=True):
        '''
        Rename a column.

        Arguments:
            rename_dict (dict): key is current column name, value is new column name. Use general column names for joint columns, e.g. put 'g' instead of 'g1', 'g2'. Only optional columns may be renamed
            inplace (bool): if True, modify in-place

        Returns:
            frame (BipartiteBase): BipartiteBase with renamed columns
        '''
        if inplace:
            frame = self
        else:
            frame = self.copy()

        for col_cur, col_new in rename_dict.items():
            if col_cur in frame.columns or col_cur in frame.columns_req or col_cur in frame.columns_opt:
                if col_cur in self.columns_opt: # If column optional
                    if len(to_list(self.reference_dict[col_cur])) > 1:
                        for i, subcol in enumerate(to_list(self.reference_dict[col_cur])):
                            DataFrame.rename(frame, {subcol: col_new + str(i + 1)}, axis=1, inplace=True)
                            frame.col_dict[subcol] = None
                    else:
                        DataFrame.rename(frame, {col_cur: col_new}, axis=1, inplace=True)
                        frame.col_dict[col_cur] = None
                    if col_cur in frame.columns_contig.keys(): # If column contiguous
                            frame.columns_contig[col_cur] = None
                            if frame.id_reference_dict: # If id_reference_dict has been initialized
                                frame.id_reference_dict[col_cur] = pd.DataFrame()
                elif col_cur not in frame._included_cols() and col_cur not in frame._included_cols(flat=True): # If column is not pre-established
                        DataFrame.rename(frame, {col_cur: col_new}, axis=1, inplace=True)
                else:
                    warnings.warn("{} is either (a) a required column and cannot be renamed or (b) a subcolumn that can be renamed, but only by specifying the general column name (e.g. use 'g' instead of 'g1' or 'g2')".format(col_cur))
            else:
                warnings.warn('{} is not in data columns'.format(col_cur))

        return frame

    def merge(self, *args, **kwargs):
        '''
        Merge two BipartiteBase objects.

        Arguments:
            *args: arguments for Pandas merge
            **kwargs: keyword arguments for Pandas merge

        Returns:
            frame (BipartiteBase): merged dataframe
        '''
        frame = DataFrame.merge(self, *args, **kwargs)
        frame = self._constructor(frame) # Use correct constructor
        if kwargs['how'] == 'left': # Non-left merge could cause issues with data, by default resets attributes
            frame._set_attributes(self)
        return frame

    def _contiguous_ids(self, id_col, copy=True):
        '''
        Make column of ids contiguous.

        Arguments:
            id_col (str): column to make contiguous ('i', 'j', or 'g'). Use general column names for joint columns, e.g. put 'j' instead of 'j1', 'j2'. Only optional columns may be renamed
            copy (bool): if False, avoid copy

        Returns:
            frame (BipartiteBase): BipartiteBase with contiguous ids
        '''
        if copy:
            frame = self.copy()
        else:
            frame = self

        cols = to_list(frame.reference_dict[id_col])
        n_cols = len(cols)
        n_rows = len(frame)
        all_ids = frame.loc[:, cols].to_numpy().reshape(n_cols * n_rows)
        # Source: https://stackoverflow.com/questions/16453465/multi-column-factorize-in-pandas
        factorized = pd.factorize(all_ids)

        # Quickly check whether ids need to be reset
        try:
            if max(factorized[1]) + 1 == len(factorized[1]):
                return frame
        except TypeError:
            # If ids are not integers, this will return a TypeError and we can ignore it
            pass

        frame.loc[:, cols] = factorized[0].reshape((n_rows, n_cols))

        # Save id reference dataframe, so user can revert back to original ids
        if frame.id_reference_dict: # If id_reference_dict has been initialized
            if len(frame.id_reference_dict[id_col]) == 0: # If dataframe empty, start with original ids: adjusted ids
                frame.id_reference_dict[id_col].loc[:, 'original_ids'] = factorized[1]
                frame.id_reference_dict[id_col].loc[:, 'adjusted_ids_1'] = np.arange(len(factorized[1]))
            else: # Merge in new adjustment step
                n_cols_id = len(frame.id_reference_dict[id_col].columns)
                id_reference_df = pd.DataFrame({'adjusted_ids_' + str(n_cols_id - 1): factorized[1], 'adjusted_ids_' + str(n_cols_id): np.arange(len(factorized[1]))}, index=np.arange(len(factorized[1]))).astype('Int64', copy=False)
                frame.id_reference_dict[id_col] = frame.id_reference_dict[id_col].merge(id_reference_df, how='left', on='adjusted_ids_' + str(n_cols_id - 1))

        # Sort columns
        frame = frame.sort_cols(copy=False)

        # ids are now contiguous
        frame.columns_contig[id_col] = True

        return frame

    def _update_cols(self, inplace=True):
        '''
        Rename columns and keep only relevant columns.

        Arguments:
            inplace (bool): if True, modify in-place

        Returns:
            frame (BipartiteBase): BipartiteBase with updated columns
        '''
        if inplace:
            frame = self
        else:
            frame = self.copy()

        new_col_dict = {}
        rename_dict = {} # For renaming columns in data
        keep_cols = []

        for key, val in frame.col_dict.items():
            if val is not None:
                rename_dict[val] = key
                new_col_dict[key] = key
                keep_cols.append(key)
            else:
                new_col_dict[key] = None
        frame.col_dict = new_col_dict
        keep_cols = sorted(keep_cols, key=col_order) # Sort columns
        DataFrame.rename(frame, rename_dict, axis=1, inplace=True)
        for col in frame.columns:
            if col not in keep_cols:
                frame.drop(col, axis=1, inplace=True)

        return frame

    def clean_data(self, clean_params=clean_params()):
        '''
        Clean data to make sure there are no NaN or duplicate observations, firms are connected by movers and firm ids are contiguous.

        Arguments:
            clean_params (ParamsDict): dictionary of parameters for cleaning. Run bpd.clean_params().describe_all() for descriptions of all valid parameters.

        Returns:
            frame (BipartiteBase): BipartiteBase with cleaned data
        '''
        self.log('beginning BipartiteBase data cleaning', level='info')

        force = clean_params['force']

        if clean_params['copy']:
            frame = self.copy()
        else:
            frame = self

        # First, correct column names
        frame.log('correcting column names', level='info')
        frame._update_cols()

        # Next, check that required columns are included and datatypes are correct
        frame.log('checking required columns and datatypes', level='info')
        frame = frame._check_cols()

        # Next, sort rows
        frame.log('sorting rows', level='info')
        frame = frame.sort_rows(is_sorted=clean_params['is_sorted'], copy=False)

        # Next, drop NaN observations
        if (not frame.no_na) or force:
            frame.log('dropping NaN observations', level='info')
            if frame.isna().to_numpy().any():
                # Checking first is considerably faster if there are no NaN observations
                frame.dropna(inplace=True)

            # Update no_na
            frame.no_na = True

        # Generate 'm' column - this is necessary for the next steps
        # 'm' will get updated in the following steps as it changes
        frame.log("generating 'm' column", level='info')
        frame = frame.gen_m(force=True, copy=False)

        # Next, make sure i-t (worker-year) observations are unique
        if (frame.i_t_unique is not None) and ((not frame.i_t_unique) or force):
            frame.log('keeping highest paying job for i-t (worker-year) duplicates', level='info')
            frame = frame._drop_i_t_duplicates(how=clean_params['i_t_how'], is_sorted=True, copy=False)

            # Update no_duplicates
            frame.no_duplicates = True
        elif (not frame.no_duplicates) or force:
            # Drop duplicate observations
            frame.log('dropping duplicate observations', level='info')
            frame.drop_duplicates(inplace=True)

            # Update no_duplicates
            frame.no_duplicates = True

        # Next, check contiguous ids before using igraph (igraph resets ids to be contiguous, so we need to make sure ours are comparable)
        for contig_col, is_contig in frame.columns_contig.items():
            if (is_contig is not None) and ((not is_contig) or force):
                frame.log('making {} ids contiguous'.format(contig_col), level='info')
                frame = frame._contiguous_ids(id_col=contig_col, copy=False)

        # Next, find largest set of firms connected by movers
        if (frame.connectedness in [False, None]) or force:
            # Generate largest connected set
            frame.log('generating largest connected set', level='info')
            frame = frame._conset(connectedness=clean_params['connectedness'], component_size_variable=clean_params['component_size_variable'], drop_multiples=clean_params['drop_multiples'], copy=False)

            # Next, check contiguous ids after igraph, in case the connected components dropped ids
            for contig_col, is_contig in frame.columns_contig.items():
                if (is_contig is not None) and (not is_contig):
                    frame.log('making {} ids contiguous'.format(contig_col), level='info')
                    frame = frame._contiguous_ids(id_col=contig_col, copy=False)

        # Sort columns
        frame.log('sorting columns', level='info')
        frame = frame.sort_cols(copy=False)

        # Reset index
        frame.reset_index(drop=True, inplace=True)

        frame.log('BipartiteBase data cleaning complete', level='info')

        return frame

    def _check_cols(self):
        '''
        Check that required columns are included, and that all columns have the correct datatype. Raises a ValueError if either is false.

        Returns:
            frame (BipartiteBase): BipartiteBase with contiguous ids, for columns that started with incorrect datatypes
        '''
        frame = self
        cols_included = True
        correct_dtypes = True

        # Check all included columns
        for col in frame._included_cols():
            for subcol in to_list(frame.reference_dict[col]):
                if subcol not in frame.columns:
                    # If column missing
                    frame.log('{} missing from data'.format(subcol), level='info')
                    cols_included = False
                else:
                    # If column included, check type
                    col_type = str(frame[subcol].dtype)
                    valid_types = to_list(frame.dtype_dict[frame.col_dtype_dict[col]])
                    if col_type not in valid_types:
                        if col in frame.columns_contig.keys():
                            # If column contiguous, we don't worry about datatype, but will log it
                            frame.log('{} has dtype {}, so we have converted it to contiguous integers'.format(subcol, col_type), level='info')
                            frame = frame._contiguous_ids(id_col=col, copy=False)
                        else:
                            frame.log('{} has wrong dtype, it is currently {} but should be one of the following: {}'.format(subcol, col_type, valid_types), level='info')
                            correct_dtypes = False

        if (not cols_included) or (not correct_dtypes):
            raise ValueError('Your data does not include the correct columns or column datatypes. The BipartitePandas object cannot be generated with your data. Please see the generated logs for more information.')

        return frame

    def _conset(self, connectedness='connected', component_size_variable='firms', drop_multiples=False, copy=True):
        '''
        Update data to include only the largest connected component of movers.

        Arguments:
            connectedness (str or None): if 'connected', keep observations in the largest connected set of firms; if 'leave_one_firm_out', keep observations in the largest leave-one-firm-out connected set; if 'leave_one_observation_out', keep observations in the largest leave-one-observation-out connected set; if None, keep all observations.
            component_size_variable (str): how to determine largest connected component. Options are 'len'/'length' (length of frame), 'firms' (number of unique firms), 'workers' (number of unique workers), 'stayers' (number of unique stayers), and 'movers' (number of unique movers).
            drop_multiples (bool): if True, rather than collapsing over spells, drop any spells with multiple observations (this is for computational efficiency when re-collapsing data for leave-one-out connected components)
            copy (bool): if False, avoid copy

        Returns:
            frame (BipartiteBase): BipartiteBase with connected component of movers
        '''
        if copy:
            frame = self.copy()
        else:
            frame = self

        if connectedness is None:
            # Skipping connected set
            frame.connectedness = None
            # frame._check_contiguous_ids() # This is necessary
            return frame

        # Keep track of whether contiguous ids change
        prev_workers = frame.n_workers()
        prev_firms = frame.n_firms()
        prev_clusters = frame.n_clusters()

        # Update data
        # Find largest connected set of firms
        # First, create graph
        G = frame._construct_graph(connectedness)
        if connectedness == 'connected':
            # Compute all connected components of firms
            cc_list = sorted(G.components(), reverse=True, key=len)
            # Iterate over connected components to find the largest
            largest_cc = cc_list[0]
            frame_largest_cc = frame.keep_ids('j', largest_cc, is_sorted=True, copy=False)
            for cc in cc_list[1:]:
                frame_cc = frame.keep_ids('j', cc, is_sorted=True, copy=False)
                replace = bpd.compare_frames(frame_cc, frame_largest_cc, size_variable=component_size_variable, operator='geq')
                if replace:
                    frame_largest_cc = frame_cc
            frame = frame_largest_cc
        elif connectedness == 'leave_one_observation_out':
            if isinstance(frame, bpd.BipartiteEventStudyBase):
                warnings.warn('You should avoid computing leave-one-observation-out connected components on event study data. It requires converting data into long format and back into event study format, which is computationally expensive.')
            # Compute all connected components of firms (each entry is a connected component)
            cc_list = G.components()
            # Keep largest leave-one-out set of firms
            frame = frame._leave_one_observation_out(cc_list=cc_list, component_size_variable=component_size_variable, drop_multiples=drop_multiples)
        elif connectedness == 'leave_one_firm_out':
            if isinstance(frame, bpd.BipartiteEventStudyBase):
                warnings.warn('You should avoid computing leave-one-firm-out components on event study data. It requires converting data into long format and back into event study format, which is computationally expensive.')
            # Compute all biconnected components of firms (each entry is a biconnected component)
            bcc_list = G.biconnected_components()
            # Keep largest leave-one-out set of firms
            frame = frame._leave_one_firm_out(bcc_list=bcc_list, component_size_variable=component_size_variable, drop_multiples=drop_multiples)

        # Data is now connected
        frame.connectedness = connectedness

        # If connected data != full data, set contiguous to False
        if prev_workers != frame.n_workers():
            frame.columns_contig['i'] = False
        if prev_firms != frame.n_firms():
            frame.columns_contig['j'] = False
        if prev_clusters is not None and prev_clusters != frame.n_clusters():
            frame.columns_contig['g'] = False

        return frame

    def _construct_graph(self, connectedness='connected'):
        '''
        Construct igraph graph linking firms by movers.

        Arguments:
            connectedness (str): if 'connected', keep observations in the largest connected set of firms; if 'leave_one_observation_out', keep observations in the largest leave-one-observation-out connected set; if 'leave_one_firm_out', keep observations in the largest leave-one-firm-out connected set; if None, keep all observations

        Returns:
            (igraph Graph): graph
        '''
        linkages_fn_dict = {
            'connected': self._construct_connected_linkages,
            'leave_one_observation_out': self._construct_biconnected_linkages,
            'leave_one_firm_out': self._construct_biconnected_linkages
        }
        # n_firms = self.loc[(self.loc[:, 'm'] > 0).to_numpy(), :].n_firms()
        return ig.Graph(edges=linkages_fn_dict[connectedness]()) # n=n_firms

    def sort_cols(self, copy=True):
        '''
        Sort frame columns (not in-place).

        Arguments:
            copy (bool): if False, avoid copy

        Returns:
            frame (BipartiteBase): BipartiteBase with columns sorted
        '''
        if copy:
            frame = self.copy()
        else:
            frame = self

        # Sort columns
        sorted_cols = sorted(frame.columns, key=col_order)
        frame = frame.reindex(sorted_cols, axis=1, copy=False)

        return frame

    def sort_rows(self, is_sorted=False, copy=True):
        '''
        Sort rows by i and t.

        Arguments:
            is_sorted (bool): if False, dataframe will be sorted by i (and t, if included). Set to True if already sorted.
            copy (bool): if False, avoid copy

        Returns:
            frame (BipartiteBase): dataframe with rows sorted
        '''
        if copy:
            frame = self.copy()
        else:
            frame = self

        if not is_sorted:
            sort_order = ['i']
            if frame._col_included('t'):
                # If t column
                sort_order.append(to_list(frame.reference_dict['t'])[0])
            ##### Disable Pandas warning #####
            pd.options.mode.chained_assignment = None
            frame.sort_values(sort_order, inplace=True)
            ##### Re-enable Pandas warning #####
            pd.options.mode.chained_assignment = 'warn'

        return frame

    def drop_rows(self, rows, drop_multiples=False, is_sorted=False, reset_index=True, copy=True):
        '''
        Drop particular rows.

        Arguments:
            rows (list): rows to keep
            drop_multiples (bool): used only if using collapsed format. If True, rather than collapsing over spells, drop any spells with multiple observations (this is for computational efficiency)
            is_sorted (bool): if False, dataframe will be sorted by i (and t, if included). Set to True if already sorted.
            reset_index (bool): if True, reset index at end
            copy (bool): if False, avoid copy

        Returns:
            frame (BipartiteBase): dataframe with given rows dropped
        '''
        rows = set(rows)
        if len(rows) == 0:
            # If nothing input
            if copy:
                return self.copy()
            return self
        self_rows = set(self.index.to_numpy())
        rows_diff = self_rows.difference(rows)

        return self.keep_rows(rows_diff, drop_multiples=drop_multiples, is_sorted=is_sorted, reset_index=reset_index, copy=copy)

    def min_movers_firms(self, threshold=15):
        '''
        List firms with at least `threshold` many movers.

        Arguments:
            threshold (int): minimum number of movers required to keep a firm

        Returns:
            valid_firms (NumPy Array): firms with sufficiently many movers
        '''
        if threshold == 0:
            # If no threshold
            return self.unique_ids('j')

        frame = self.loc[self.loc[:, 'm'].to_numpy() > 0, :]

        return frame.min_workers_firms(threshold)

    @recollapse_loop(True)
    def min_movers_frame(self, threshold=15, drop_multiples=False, is_sorted=False, reset_index=True, copy=True):
        '''
        Return dataframe of firms with at least `threshold` many movers.

        Arguments:
            threshold (int): minimum number of movers required to keep a firm
            drop_multiples (bool): used only for collapsed format. If True, rather than collapsing over spells, drop any spells with multiple observations (this is for computational efficiency)
            is_sorted (bool): if False, dataframe will be sorted by i (and t, if included). Set to True if already sorted.
            reset_index (bool): if True, reset index at end
            copy (bool): if False, avoid copy

        Returns:
            (BipartiteBase): dataframe of firms with sufficiently many movers
        '''
        if threshold == 0:
            # If no threshold
            if copy:
                return self.copy()
            return self

        valid_firms = self.min_movers_firms(threshold)

        return self.keep_ids('j', keep_ids_list=valid_firms, drop_multiples=drop_multiples, is_sorted=is_sorted, reset_index=reset_index, copy=copy)

    def cluster(self, measures=bpd.measures.cdfs(), grouping=bpd.grouping.kmeans(), stayers_movers=None, t=None, weighted=True, dropna=False, clean_params=None, is_sorted=False, copy=False):
        '''
        Cluster data and assign a new column giving the cluster for each firm.

        Arguments:
            measures (function or list of functions): how to compute measures for clustering. Options can be seen in bipartitepandas.measures.
            grouping (function): how to group firms based on measures. Options can be seen in bipartitepandas.grouping.
            stayers_movers (str or None): if None, clusters on entire dataset; if 'stayers', clusters on only stayers; if 'movers', clusters on only movers
            t (int or None): if None, clusters on entire dataset; if int, gives period in data to consider (only valid for non-collapsed data)
            weighted (bool): if True, weight firm clusters by firm size (if a weight column is included, firm weight is computed using this column; otherwise, each observation has weight 1)
            dropna (bool): if True, drop observations where firms aren't clustered; if False, keep all observations
            clean_params (None or ParamsDict): dictionary of parameters for cleaning. This is used when observations get dropped because they were not clustered. Default is None, which sets connectedness to be the connectedness measure previously used. Run bpd.clean_params().describe_all() for descriptions of all valid parameters.
            is_sorted (bool): for event study format. If False, dataframe will be sorted by i (and t, if included). Set to True if already sorted.
            copy (bool): if False, avoid copy

        Returns:
            frame (BipartiteBase): BipartiteBase with clusters
        '''
        if copy:
            frame = self.copy()
        else:
            frame = self

        # Prepare data for clustering
        cluster_data, weights, jids = frame._prep_cluster(stayers_movers=stayers_movers, t=t, weighted=weighted, is_sorted=is_sorted, copy=False)

        # Compute measures
        for i, measure in enumerate(to_list(measures)):
            if i == 0:
                computed_measures = measure(cluster_data, jids)
            else:
                # For computing both cdfs and moments
                computed_measures = np.concatenate([computed_measures, measure(cluster_data, jids)], axis=1)
        frame.log('firm moments computed', level='info')

        # Can't group using quantiles if more than 1 column
        if (grouping.__name__ == 'compute_quantiles') and (computed_measures.shape[1] > 1):
            grouping = bpd.measures.kmeans()
            warnings.warn('Cannot cluster using quantiles if multiple measures computed. Defaulting to KMeans.')

        # Compute firm groups
        frame.log('computing firm groups', level='info')
        clusters = grouping(computed_measures, weights)
        frame.log('firm groups computed', level='info')

        # Link firms to clusters
        clusters_dict = dict(pd._lib.fast_zip([jids, clusters]))
        frame.log('dictionary linking firms to clusters generated', level='info')

        # Drop columns (because prepared data is not always a copy, must drop from self)
        for col in ['row_weights', 'one']:
            if col in self.columns:
                self.drop(col, axis=1, inplace=True)
            if col in frame.columns:
                frame.drop(col, axis=1, inplace=True)

        # Drop existing clusters
        if frame._col_included('g'):
            frame.drop('g', axis=1, inplace=True)

        for i, j_col in enumerate(to_list(frame.reference_dict['j'])):
            if len(to_list(frame.reference_dict['j'])) == 1:
                g_col = 'g'
            elif len(to_list(frame.reference_dict['j'])) == 2:
                g_col = 'g' + str(i + 1)

            # Merge into event study data
            frame[g_col] = frame[j_col].map(clusters_dict)
            # Keep column as int even with nans
            frame.loc[:, g_col] = frame.loc[:, g_col].astype('Int64', copy=False)
            frame.col_dict[g_col] = g_col

        # Sort columns
        frame = frame.sort_cols(copy=False)

        if dropna:
            # Drop firms that don't get clustered
            frame.dropna(inplace=True)
            frame.reset_index(drop=True, inplace=True)
            frame.loc[:, frame.reference_dict['g']] = frame.loc[:, frame.reference_dict['g']].astype(int, copy=False)
            # Clean data
            if clean_params is None:
                frame = frame.clean_data(clean_params({'connectedness': frame.connectedness}))
            else:
                frame = frame.clean_data(clean_params)

        frame.columns_contig['g'] = True

        frame.log('clusters merged into data', level='info')

        return frame
