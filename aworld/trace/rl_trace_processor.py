# coding: utf-8
"""
rl_trace_processor.py
Used to clean raw trace data into standard storage structure for reinforcement learning training.
"""
import json
import os
from cgitb import enable
from typing import List, Dict, Any
from datetime import datetime
from opentelemetry.sdk.trace import Span
from aworld.logs.util import logger
import oss2

class ExpMeta:
    def __init__(self, task_id: str, task_name: str, agent_id: str, step: int, execute_time: float, pre_agent: str = None):
        self.task_id = task_id
        self.task_name = task_name
        self.agent_id = agent_id
        self.step = step
        self.execute_time = execute_time
        self.pre_agent = pre_agent

    def to_dict(self):
        return {
            "task_id": self.task_id,
            "task_name": self.task_name,
            "agent_id": self.agent_id,
            "step": self.step,
            "execute_time": self.execute_time,
            "pre_agent": self.pre_agent
        }

class Experience:
    def __init__(self, observation: Any, action: Any, reward_t: float, adv_t: float = 0.0, v_t: float = 0.0, messages: List[Dict] = None):
        self.observation = observation
        self.action = action
        self.reward_t = reward_t
        self.adv_t = adv_t
        self.v_t = v_t
        self.messages = messages

    def to_dict(self):
        return {
            "observation": self.observation,
            "action": self.action,
            "reward_t": self.reward_t,
            "adv_t": self.adv_t,
            "v_t": self.v_t,
            "messages": self.messages
        }

class DataRow:
    def __init__(self, id_: str, exp_meta: ExpMeta, exp_data: Experience):
        self.id = id_
        self.exp_meta = exp_meta
        self.exp_data = exp_data

    def to_dict(self):
        return {
            "id": self.id,
            "exp_meta": self.exp_meta.to_dict(),
            "exp_data": self.exp_data.to_dict()
        }

class ReplayBufferExporter:
    @classmethod
    def replay_buffer_exporter(cls, spans: list[dict[str, Any]], output_dir: str):
        """
        Process spans, only process spans with 'step_execution_' prefix, and group by task_id to output to different files

        Args:
            spans: span data list
            output_dir: output directory path
        """
        # Ensure output directory exists
        os.makedirs(output_dir, exist_ok=True)
        
        # Get OSS credentials from environment variables
        enable_oss_export = os.getenv("EXPORT_REPLAY_TRACE_TO_OSS", "false").lower() == "true"
        access_key_id = os.getenv('OSS_ACCESS_KEY_ID')
        access_key_secret = os.getenv('OSS_ACCESS_KEY_SECRET')
        endpoint = os.getenv('OSS_ENDPOINT')
        bucket_name = os.getenv('OSS_BUCKET_NAME')
        bucket = None
        
        if not all([access_key_id, access_key_secret, endpoint, bucket_name]):
            enable_oss_export = False
            logger.warn("Missing required OSS environment variables")
        else:
            try:
                # Initialize OSS client
                auth = oss2.Auth(access_key_id, access_key_secret)
                bucket = oss2.Bucket(auth, endpoint, bucket_name)
            except Exception as e:
                logger.warn(f"Failed to initialize OSS client, endpoint: {endpoint}, bucket: {bucket_name}. Error: {str(e)}")
        
        # Group by task_id
        task_groups = {}

        for span_data in spans:
            # Only process spans with 'step_execution_' prefix
            if not span_data['name'].startswith('step_execution_'):
                continue
                
            attr = span_data.get('attributes', {})
            exp_id = attr.get('exp_id')
            task_id = attr.get('task_id', '')
            
            if not exp_id or not task_id:
                continue
                
            if task_id not in task_groups:
                task_groups[task_id] = {}
                
            if exp_id not in task_groups[task_id]:
                task_groups[task_id][exp_id] = {
                    'exp_meta': None,
                    'exp_data': None
                }
                
            # Process step_execution span
            task_name = attr.get('task_name', '')
            agent_id = attr.get('agent_id', '')
            step = attr.get('step', 0)
            execute_time = float(span_data.get('start_time', 0).split('.')[0].replace(' ', '').replace('-', '').replace(':', ''))
            
            observation = {}
            action = []
            messages = []
            pre_agent = None
            if 'observation' in attr:
                try:
                    observation = json.loads(attr['observation'])
                except:
                    observation = attr['observation']
                    
            if 'actions' in attr:
                try:
                    action = json.loads(attr['actions'])
                except:
                    action = attr['actions']

            if 'messages' in attr:
                try:
                    messages = json.loads(attr['messages'])
                except:
                    messages = attr['messages']

            pre_agent = attr.get('pre_agent', '')
            reward = attr.get('reward', 0.0)
            adv = attr.get('adv_t', 0.0)
            v = attr.get('v_t', 0.0)
            
            exp_meta = ExpMeta(task_id, task_name, agent_id, step, execute_time, pre_agent)
            exp_data = Experience(observation, action, reward, adv, v, messages)
            
            task_groups[task_id][exp_id]['exp_meta'] = exp_meta
            task_groups[task_id][exp_id]['exp_data'] = exp_data
            
        # Process data for each task_id
        for task_id, exp_groups in task_groups.items():
            # Merge data and generate final Experience object
            data_rows = []
            
            # Read existing data (if any)
            output_path = os.path.join(output_dir, f"task_replay_{task_id}.json")
            if os.path.exists(output_path):
                try:
                    with open(output_path, 'r', encoding='utf-8') as f:
                        existing_data = json.load(f)
                        data_rows.extend([DataRow(
                            row['id'],
                            ExpMeta(**row['exp_meta']),
                            Experience(**row['exp_data'])
                        ) for row in existing_data])
                except Exception as e:
                    logger.warn(f"Failed to read existing file {output_path}: {str(e)}")
            
            # Add new data
            for exp_id, group in exp_groups.items():
                if group['exp_meta'] and group['exp_data']:
                    row = DataRow(exp_id, group['exp_meta'], group['exp_data'])
                    data_rows.append(row)
                
            # Sort by execute_time
            data_rows.sort(key=lambda x: x.exp_meta.execute_time)
            
            # Export to json
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump([row.to_dict() for row in data_rows], f, ensure_ascii=False, indent=2)
            logger.info(f"Processing completed, exported {len(data_rows)} experiences to {output_path}")

            if enable_oss_export:
                # Upload to OSS
                try:
                    # Get the relative path
                    abs_path = os.path.abspath(output_path)
                    path_parts = abs_path.split(os.sep)
                    if len(path_parts) >= 4:
                        # Get the last 4 parts of the path
                        relative_path = os.sep.join(path_parts[-4:])
                        oss_key = relative_path
                    else:
                        oss_key = f"replay_buffer/{os.path.basename(output_path)}"
                    bucket.put_object_from_file(oss_key, output_path)
                    logger.info(f"Successfully uploaded {output_path} to OSS: {oss_key}")
                except Exception as e:
                    logger.warn(f"Failed to upload {output_path} to OSS: {str(e)}")

            
