import asyncio
import logging
import string
import uuid
import datetime
import time
import random
from typing import Optional

from typing_extensions import List, Any, Dict

from aworld.sandbox.base.apibase import SandboxApiBase
from aworld.sandbox.env_client.kubernetes.client import KubernetesApiClient
from aworld.sandbox.models import SandboxCreateResponse, EnvConfig, SandboxEnvType, SandboxStatus, SandboxK8sResponse, \
    SandboxInfo
from aworld.sandbox.run.mcp_servers import McpServers


class SandboxApi(SandboxApiBase):

    @classmethod
    def _create_sandbox(
            cls,
            env_type: int,
            env_config: EnvConfig,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Optional[Any] = None,
    ) -> SandboxCreateResponse:
        #1. Build environment image based on EnvConfig
        #todo

        #2. Build actual environment based on env_type
        if env_type == SandboxEnvType.K8S:
            return cls._create_sandbox_by_k8s(mcp_servers,mcp_config)
        else:
            return None

    @classmethod
    def _create_sandbox_by_k8s(
            cls,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Optional[Any] = None,
    ) -> SandboxK8sResponse:
        """
        Create sandbox based on k8s yaml file
        """
        # Initialize these variables outside the try block to avoid accessing undefined variables in exception handling
        client = None
        pod_name = None
        service_name = None

        try:
            sandbox_id = str(uuid.uuid4())
            # Generate current date and time as prefix, format is yymmddHHMMSS
            date_prefix = datetime.datetime.now().strftime("%y%m%d%H%M%S")
            random_str = cls.generate_random_string()
            pod_name = f"pod-{date_prefix}-{random_str}"
            service_name = f"service-{date_prefix}-{random_str}"
            logging.info(f"Generated pod_name: {pod_name}")
            logging.info(f"Generated service_name: {service_name}")

            client = KubernetesApiClient()

            pod_result = client.create_pod_from_yaml(pod_name=pod_name)
            if not pod_result:
                return None
            max_attempts = 30
            attempts = 0
            wait_seconds = 2
            pod_ready = False
            pod_info = None
            while attempts < max_attempts:
                pod_info = client.get_pod_info(pod_name)
                pod_ready = pod_info and pod_info.get("status") == SandboxStatus.RUNNING
                if pod_ready:
                    break
                attempts += 1
                if attempts < max_attempts:
                    logging.info(f"Waiting for Pod to be ready, attempt {attempts}/{max_attempts}")
                    time.sleep(wait_seconds)
            if not pod_ready:
                logging.warning("Timed out waiting for Pod and Service to be ready")
                client.delete_pod(pod_name)
                return None

            service_result = client.create_service_from_yaml(service_name=service_name, selector_name=pod_name)
            if not service_result:
                client.delete_pod(pod_name)
                return None

            max_attempts = 30
            attempts = 0
            wait_seconds = 2
            service_ready = False
            service_info = None
            while attempts < max_attempts:
                service_info = client.get_service_info(service_name)
                if service_info and  ('LoadBalancer' == service_info.get("type") and service_info.get('host')):
                    service_ready = True
                elif service_info and 'ClusterIP' == service_info.get("type"):
                    service_ready = True
                if service_ready:
                    time.sleep(wait_seconds)
                    break
                attempts += 1
                if attempts < max_attempts:
                    logging.info(f"Waiting for Service to be ready, attempt {attempts}/{max_attempts}")
                    time.sleep(wait_seconds)
            if not service_ready:
                client.delete_pod(pod_name)
                client.delete_service(service_name)
                return None

            if pod_ready and service_ready:
                if mcp_servers:
                    # Use asyncio.run to call async method
                    try:
                        metadata={
                            sandbox_id: sandbox_id,
                            "pod_name": pod_name,
                            "service_name": service_name,
                            "status": pod_info.get("status"),
                            "cluster_ip": service_info.get("cluster_ip"),
                            "host": service_info.get("host"),
                        }
                        response = SandboxApi._get_mcp_configs(
                            mcp_servers=mcp_servers,
                            mcp_config=mcp_config,
                            metadata=metadata,
                            env_type=SandboxEnvType.K8S
                        )
                        if response:
                            mcp_config = response
                    except Exception as e:
                        logging.warning(f"Failed to get mcp configs: {e}")


            return SandboxK8sResponse(
                sandbox_id=sandbox_id,
                pod_name=pod_name,
                service_name=service_name,
                status=pod_info.get("status"),
                cluster_ip=service_info.get("cluster_ip"),
                host=service_info.get("host"),
                mcp_config=mcp_config,
            )

        except Exception as e:
            logging.info(f"Failed to create Sandbox by k8s: {e}")
            # Only attempt to delete resources if client has been initialized
            if client:
                if pod_name:
                    client.delete_pod(pod_name)
                if service_name:
                    client.delete_service(service_name)
            return None

    # Generate random string (combination of letters and numbers)
    @classmethod
    def generate_random_string(cls, length=6):
        characters = string.ascii_lowercase + string.digits
        return ''.join(random.choice(characters) for _ in range(length))

    @classmethod
    def _get_mcp_configs(
            cls,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Optional[Any] = None,
            metadata: Optional[Dict[str, str]] = None,
            env_type: Optional[int] = None,
    )->Any:
        try:
            # Create McpServers instance
            mcp_servers_instance = McpServers(mcp_servers, mcp_config)

            return mcp_servers_instance.get_mcp_configs(
                mcp_servers=mcp_servers, 
                mcp_config=mcp_config, 
                metadata=metadata,
                env_type=env_type
            )
        except Exception as e:
            logging.warning(f"Failed to get_mcp_configs: {e}")
            return None

    @classmethod
    async def _remove_sandbox(
            cls,
            sandbox_id: Optional[str] = None,
            metadata: Optional[Dict[str, str]] = None,
            env_type: Optional[int] = None,
    ) -> None:
        try:
            if not sandbox_id or not sandbox_id or not metadata or not env_type or env_type != SandboxEnvType.K8S:
                logging.warning(
                    f"sandbox_id={sandbox_id} or metadata={metadata} or env_type={env_type} is None or env_type is not K8S")
                return False

            pod_name = metadata.get("pod_name")
            service_name = metadata.get("service_name")
            if not pod_name or not service_name:
                logging.warning(f"pod_name={pod_name} or service_name={service_name} is None")
                return False
            client = KubernetesApiClient()
            client.delete_pod(pod_name)
            client.delete_service(service_name)
        except Exception as e:
            logging.warning(f"Failed to remove Sandbox: {e}")
            return False




