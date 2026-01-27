# coding: utf-8
# Copyright (c) inclusionAI.
import os

from aworld.evaluations import _auto_discover_scorers

_auto_discover_scorers(current_dir=os.path.join(os.path.dirname(__file__), 'validate'),
                       package_name=f'{__name__}.validate')
