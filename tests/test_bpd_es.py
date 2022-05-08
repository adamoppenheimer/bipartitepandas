'''
Tests for bipartitepandas.
'''
import pytest
import numpy as np
import pandas as pd
import bipartitepandas as bpd
import pickle

#########################################
##### Tests for BipartiteEventStudy #####
#########################################

def test_event_study_1():
    # Test constructor for BipartiteEventStudy.
    worker_data = []
    # Worker 0
    worker_data.append({'i': 0, 'j': 0, 'y': 2., 't': 1})
    worker_data.append({'i': 0, 'j': 1, 'y': 1., 't': 2})
    # Worker 1
    worker_data.append({'i': 1, 'j': 1, 'y': 1., 't': 1})
    worker_data.append({'i': 1, 'j': 2, 'y': 1., 't': 2})
    # Worker 3
    worker_data.append({'i': 3, 'j': 2, 'y': 1., 't': 1})
    worker_data.append({'i': 3, 'j': 2, 'y': 2., 't': 2})

    df = pd.concat([pd.DataFrame(worker, index=[i]) for i, worker in enumerate(worker_data)])

    bdf = bpd.BipartiteLong(data=df)
    bdf = bdf.clean()
    df = pd.DataFrame(bdf.to_eventstudy())
    bdf = bpd.BipartiteEventStudy(df)
    bdf = bdf.clean()

    stayers = bdf[bdf['m'] == 0]
    movers = bdf[bdf['m'] > 0]

    assert stayers.iloc[0]['i'] == 2
    assert stayers.iloc[0]['j1'] == 2
    assert stayers.iloc[0]['j2'] == 2
    assert stayers.iloc[0]['y1'] == 1
    assert stayers.iloc[0]['y2'] == 1
    assert stayers.iloc[0]['t1'] == 1
    assert stayers.iloc[0]['t2'] == 1

    assert stayers.iloc[1]['i'] == 2
    assert stayers.iloc[1]['j1'] == 2
    assert stayers.iloc[1]['j2'] == 2
    assert stayers.iloc[1]['y1'] == 2
    assert stayers.iloc[1]['y2'] == 2
    assert stayers.iloc[1]['t1'] == 2
    assert stayers.iloc[1]['t2'] == 2

    assert movers.iloc[0]['i'] == 0
    assert movers.iloc[0]['j1'] == 0
    assert movers.iloc[0]['j2'] == 1
    assert movers.iloc[0]['y1'] == 2
    assert movers.iloc[0]['y2'] == 1
    assert movers.iloc[0]['t1'] == 1
    assert movers.iloc[0]['t2'] == 2

    assert movers.iloc[1]['i'] == 1
    assert movers.iloc[1]['j1'] == 1
    assert movers.iloc[1]['j2'] == 2
    assert movers.iloc[1]['y1'] == 1
    assert movers.iloc[1]['y2'] == 1
    assert movers.iloc[1]['t1'] == 1
    assert movers.iloc[1]['t2'] == 2

def test_get_cs_2():
    # Test get_cs() for BipartiteEventStudy.
    worker_data = []
    # Worker 0
    worker_data.append({'i': 0, 'j': 0, 'y': 2., 't': 1})
    worker_data.append({'i': 0, 'j': 1, 'y': 1., 't': 2})
    # Worker 1
    worker_data.append({'i': 1, 'j': 1, 'y': 1., 't': 1})
    worker_data.append({'i': 1, 'j': 2, 'y': 1., 't': 2})
    # Worker 3
    worker_data.append({'i': 3, 'j': 2, 'y': 1., 't': 1})
    worker_data.append({'i': 3, 'j': 2, 'y': 2., 't': 2})

    df = pd.concat([pd.DataFrame(worker, index=[i]) for i, worker in enumerate(worker_data)])

    bdf = bpd.BipartiteLong(data=df)
    bdf = bdf.clean().to_eventstudy().clean().get_cs(copy=False)

    stayers = bdf[bdf['m'] == 0]
    movers1 = bdf[(bdf['m'] > 0) & (bdf['cs'] == 1)]
    movers0 = bdf[(bdf['m'] > 0) & (bdf['cs'] == 0)]

    assert stayers.iloc[0]['i'] == 2
    assert stayers.iloc[0]['j1'] == 2
    assert stayers.iloc[0]['j2'] == 2
    assert stayers.iloc[0]['y1'] == 1
    assert stayers.iloc[0]['y2'] == 1
    assert stayers.iloc[0]['t1'] == 1
    assert stayers.iloc[0]['t2'] == 1

    assert stayers.iloc[1]['i'] == 2
    assert stayers.iloc[1]['j1'] == 2
    assert stayers.iloc[1]['j2'] == 2
    assert stayers.iloc[1]['y1'] == 2
    assert stayers.iloc[1]['y2'] == 2
    assert stayers.iloc[1]['t1'] == 2
    assert stayers.iloc[1]['t2'] == 2

    assert movers1.iloc[0]['i'] == 0
    assert movers1.iloc[0]['j1'] == 0
    assert movers1.iloc[0]['j2'] == 1
    assert movers1.iloc[0]['y1'] == 2
    assert movers1.iloc[0]['y2'] == 1
    assert movers1.iloc[0]['t1'] == 1
    assert movers1.iloc[0]['t2'] == 2

    assert movers1.iloc[1]['i'] == 1
    assert movers1.iloc[1]['j1'] == 1
    assert movers1.iloc[1]['j2'] == 2
    assert movers1.iloc[1]['y1'] == 1
    assert movers1.iloc[1]['y2'] == 1
    assert movers1.iloc[1]['t1'] == 1
    assert movers1.iloc[1]['t2'] == 2

    assert movers0.iloc[0]['i'] == 0
    assert movers0.iloc[0]['j1'] == 1
    assert movers0.iloc[0]['j2'] == 0
    assert movers0.iloc[0]['y1'] == 1
    assert movers0.iloc[0]['y2'] == 2
    assert movers0.iloc[0]['t1'] == 2
    assert movers0.iloc[0]['t2'] == 1

    assert movers0.iloc[1]['i'] == 1
    assert movers0.iloc[1]['j1'] == 2
    assert movers0.iloc[1]['j2'] == 1
    assert movers0.iloc[1]['y1'] == 1
    assert movers0.iloc[1]['y2'] == 1
    assert movers0.iloc[1]['t1'] == 2
    assert movers0.iloc[1]['t2'] == 1
