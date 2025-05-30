# Initialize AworldTaskClient with server endpoints
import asyncio
import logging
from datetime import datetime
import os
import random
import uuid

from aworld.utils.common import get_local_ip

from client.aworld_client import AworldTask, AworldTaskClient

AWORLD_TASK_CLIENT = AworldTaskClient(
    know_hosts = ["nlb-lm8m8hityawuxbxvof.ap-southeast-1.nlb.aliyuncsslbintl.com:9099"]
)


async def _run_gaia_task(gaia_task: AworldTask, delay: int) -> None:
    """Run a single Gaia task with the given question ID.

    Args:
        gaia_task_id: The ID of the question to process
    """
    global AWORLD_TASK_CLIENT
    await asyncio.sleep(delay)

    # Submit task to Aworld server
    await AWORLD_TASK_CLIENT.submit_task(gaia_task)

    # Get and print task result
    task_result = await AWORLD_TASK_CLIENT.get_task_state(task_id=gaia_task.task_id)
    logging.info(f"execute task_result#{gaia_task.task_id} is {task_result.data}")


async def _batch_run_gaia_task(gaia_tasks: list[AworldTask]) -> None:
    """Run multiple Gaia tasks in parallel.

    """
    tasks = [
        _run_gaia_task(gaia_task, index * 3)
        for index,gaia_task in enumerate(gaia_tasks)
    ]
    await asyncio.gather(*tasks)


CUSTOM_SYSTEM_PROMPT = f""" **PLEASE CUSTOM IT **"""

if __name__ == '__main__':
    gaia_task_ids = ['c61d22de-5f6c-4958-a7f6-5e9707bd3466', '17b5a6a3-bc87-42e8-b0fb-6ab0781ef2cc', '04a04a9b-226c-43fd-b319-d5e89743676f', '14569e28-c88c-43e4-8c32-097d35b9a67d', 'e1fc63a2-da7a-432f-be78-7c4a95598703', '32102e3e-d12a-4209-9163-7b3a104efe5d', '8e867cd7-cff9-4e6c-867a-ff5ddc2550be', '3627a8be-a77f-41bb-b807-7e1bd4c0ebdf', '7619a514-5fa8-43ef-9143-83b66a43d7a4', 'ec09fa32-d03f-4bf8-84b0-1f16922c3ae4', '676e5e31-a554-4acc-9286-b60d90a92d26', '7dd30055-0198-452e-8c25-f73dbe27dcb8', '2a649bb1-795f-4a01-b3be-9a01868dae73', '87c610df-bef7-4932-b950-1d83ef4e282b', '624cbf11-6a41-4692-af9c-36b3e5ca3130', 'dd3c7503-f62a-4bd0-9f67-1b63b94194cc', '5d0080cb-90d7-4712-bc33-848150e917d3', 'bec74516-02fc-48dc-b202-55e78d0e17cf', 'a1e91b78-d3d8-4675-bb8d-62741b4b68a6', '46719c30-f4c3-4cad-be07-d5cb21eee6bb', 'df6561b2-7ee5-4540-baab-5095f742716a', '00d579ea-0889-4fd9-a771-2c8d79835c8d', '4b6bb5f7-f634-410e-815d-e673ab7f8632', 'f0f46385-fc03-4599-b5d3-f56496c3e69f', '384d0dd8-e8a4-4cfe-963c-d37f256e7662', 'e4e91f1c-1dcd-439e-9fdd-cb976f5293fd', '56137764-b4e0-45b8-9c52-1866420c3df5', 'de9887f5-ead8-4727-876f-5a4078f8598c', 'cffe0e32-c9a6-4c52-9877-78ceb4aaa9fb', '8b3379c0-0981-4f5b-8407-6444610cb212', '0ff53813-3367-4f43-bcbd-3fd725c1bf4b', '983bba7c-c092-455f-b6c9-7857003d48fc', 'a7feb290-76bb-4cb7-8800-7edaf7954f2f', 'b4cc024b-3f5e-480e-b96a-6656493255b5', '2d83110e-a098-4ebb-9987-066c06fa42d0', '33d8ea3b-6c6b-4ff1-803d-7e270dea8a57', '5cfb274c-0207-4aa7-9575-6ac0bd95d9b2', '9b54f9d9-35ee-4a14-b62f-d130ea00317f', 'e8cb5b03-41e0-4086-99e5-f6806cd97211', '27d5d136-8563-469e-92bf-fd103c28b57c', 'dc28cf18-6431-458b-83ef-64b3ce566c10', 'b816bfce-3d80-4913-a07d-69b752ce6377', 'f46b4380-207e-4434-820b-f32ce04ae2a4', '72e110e7-464c-453c-a309-90a95aed6538', '05407167-39ec-4d3a-a234-73a9120c325d', 'b9763138-c053-4832-9f55-86200cb1f99c', '16d825ff-1623-4176-a5b5-42e0f5c2b0ac', '2b3ef98c-cc05-450b-a719-711aee40ac65', 'bfcd99e1-0690-4b53-a85c-0174a8629083', '544b7f0c-173a-4377-8d56-57b36eb26ddf', '42576abe-0deb-4869-8c63-225c2d75a95a', '6b078778-0b90-464d-83f6-59511c811b01', 'b415aba4-4b68-4fc6-9b89-2c812e55a3e1', '076c8171-9b3b-49b9-a477-244d2a532826', '08cae58d-4084-4616-b6dd-dd6534e4825b', 'cca530fc-4052-43b2-b130-b30968d8aa44', '2dfc4c37-fec1-4518-84a7-10095d30ad75', '935e2cff-ae78-4218-b3f5-115589b19dae', '4fc2f1ae-8625-45b5-ab34-ad4433bc21f8', '5188369a-3bbe-43d8-8b94-11558f909a08', '9f41b083-683e-4dcf-9185-ccfeaa88fa45', '6f37996b-2ac7-44b0-8e68-6d28256631b4', '56db2318-640f-477a-a82f-bc93ad13e882', 'ecbc4f94-95a3-4cc7-b255-6741a458a625', 'e9a2c537-8232-4c3f-85b0-b52de6bcba99', '8131e2c0-0083-4265-9ce7-78c2d568425d', '9318445f-fe6a-4e1b-acbf-c68228c9906a', '71345b0a-9c7d-4b50-b2bf-937ec5879845', '72c06643-a2fa-4186-aa5c-9ec33ae9b445', 'ebbc1f13-d24d-40df-9068-adcf735b4240', '7b5377b0-3f38-4103-8ad2-90fe89864c04', '114d5fd0-e2ae-4b6d-a65a-870da2d19c08', '8f80e01c-1296-4371-9486-bb3d68651a60', 'ad37a656-079a-49f9-a493-7b739c9167d1', '366e2f2b-8632-4ef2-81eb-bc3877489217', 'c526d8d6-5987-4da9-b24c-83466fa172f3', 'f3917a3d-1d17-4ee2-90c5-683b072218fe', '389793a7-ca17-4e82-81cb-2b3a2391b4b9', '4b650a35-8529-4695-89ed-8dc7a500a498', '3da89939-209c-4086-8520-7eb734e6b4ef', '48eb8242-1099-4c26-95d4-ef22b002457a', 'c8b7e059-c60d-472e-ad64-3b04ae1166dc', 'd1af70ea-a9a4-421a-b9cc-94b5e02f1788', 'a3fbeb63-0e8c-4a11-bff6-0e3b484c3e9c', '8d46b8d6-b38a-47ff-ac74-cda14cf2d19b', '08f3a05f-5947-4089-a4c4-d4bcfaa6b7a0', 'c714ab3a-da30-4603-bacd-d008800188b9', '9d191bce-651d-4746-be2d-7ef8ecadb9c2', '54612da3-fd56-4941-80f4-5eb82330de25', 'ded28325-3447-4c56-860f-e497d6fb3577', '6359a0b1-8f7b-499b-9336-840f9ab90688', 'e961a717-6b25-4175-8a68-874d28190ee4', '7cc4acfa-63fd-4acc-a1a1-e8e529e0a97f', 'd700d50d-c707-4dca-90dc-4528cddd0c80', '65afbc8a-89ca-4ad5-8d62-355bb401f61d', '851e570a-e3de-4d84-bcfa-cc85578baa59', 'cabe07ed-9eca-40ea-8ead-410ef5e83f91', '0a3cd321-3e76-4622-911b-0fda2e5d6b1a', 'f2feb6a4-363c-4c09-a804-0db564eafd68', '3cef3a44-215e-4aed-8e3b-b1e3f08063b7', '50f58759-7bd6-406f-9b0d-5692beb2a926', '0b260a57-3f3a-4405-9f29-6d7a1012dbfb', 'ed58682d-bc52-4baa-9eb0-4eb81e1edacc', 'cca70ce6-1952-45d2-acd4-80c903b0bc49', '872bfbb1-9ccf-49f6-8c5f-aa22818ccd66', '99c9cc74-fdc8-46c6-8f8d-3ce2d3bfeea3', 'b7f857e4-d8aa-4387-af2a-0e844df5b9d8', 'd8152ad6-e4d5-4c12-8bb7-8d57dc10c6de', '67e8878b-5cef-4375-804e-e6291fdbe78a', 'c3a79cfe-8206-451f-aca8-3fec8ebe51d3', 'd0633230-7067-47a9-9dbf-ee11e0a2cdd6', '023e9d44-96ae-4eed-b912-244ee8c3b994', '305ac316-eef6-4446-960a-92d80d542f82', '0e9e85b8-52b9-4de4-b402-5f635ab9631f', '20194330-9976-4043-8632-f8485c6c71b2', '4d51c4bf-4b0e-4f3d-897b-3f6687a7d9f2', '0383a3ee-47a7-41a4-b493-519bdefe0488', '65638e28-7f37-4fa7-b7b9-8c19bb609879', '3ff6b7a9-a5bd-4412-ad92-0cd0d45c0fee', 'f918266a-b3e0-4914-865d-4faa564f1aef', '708b99c5-e4a7-49cb-a5cf-933c8d46470d', '0a65cb96-cb6e-4a6a-8aae-c1084f613456', '11af4e1a-5f45-467d-9aeb-46f4bb0bf034', 'e142056d-56ab-4352-b091-b56054bd1359', '50ad0280-0819-4bd9-b275-5de32d3b5bcb', '65da0822-a48a-4a68-bbad-8ed1b835a834', 'da52d699-e8d2-4dc5-9191-a2199e0b6a9b', '0bb3b44a-ede5-4db5-a520-4e844b0079c5', '7673d772-ef80-4f0f-a602-1bf4485c9b43', '73c1b9fe-ee1d-4cf4-96ca-35c08f97b054', 'c365c1c7-a3db-4d5e-a9a1-66f56eae7865', 'ad2b4d70-9314-4fe6-bfbe-894a45f6055f', '5b2a14e8-6e59-479c-80e3-4696e8980152', '7d4a7d1d-cac6-44a8-96e8-ea9584a70825', 'dc22a632-937f-4e6a-b72f-ba0ff3f5ff97', 'e2d69698-bc99-4e85-9880-67eaccd66e6c', '3f57289b-8c60-48be-bd80-01f8099ca449', 'a56f1527-3abf-41d6-91f8-7296d6336c3f', '23dd907f-1261-4488-b21c-e9185af91d5e', '42d4198c-5895-4f0a-b0c0-424a66465d83', 'edd4d4f2-1a58-45c4-b038-67337af4e029', 'a26649c6-1cb2-470a-871e-6910c64c3e53', '4d0aa727-86b1-406b-9b33-f870dd14a4a5', '1f975693-876d-457b-a649-393859e79bf3', 'd5141ca5-e7a0-469f-bf3e-e773507c86e2', '9e1fc53b-46ff-49a1-9d05-9e6faac34cc5', '840bfca7-4f7b-481a-8794-c560c340185d', '1dcc160f-c187-48c2-b68e-319bd4354f3d', 'b2c257e0-3ad7-4f05-b8e3-d9da973be36e', 'e0c10771-d627-4fd7-9694-05348e54ee36', 'a0068077-79f4-461a-adfe-75c1a4148545', 'e29834fd-413a-455c-a33e-c3915b07401c', 'bda648d7-d618-4883-88f4-3466eabd860e', '50ec8903-b81f-4257-9450-1085afd2c319', 'cf106601-ab4f-4af9-b045-5295fe67b37d', '5f982798-16b9-4051-ab57-cfc7ebdb2a91', 'a0c07678-e491-4bbc-8f0b-07405144218f', '7bd855d8-463d-4ed5-93ca-5fe35145f733', '5a0c1adf-205e-4841-a666-7c3ef95def9d', '0512426f-4d28-49f0-be77-06d05daec096', '0bdb7c40-671d-4ad1-9ce3-986b159c0ddc', '08c0b6e9-1b43-4c2e-ae55-4e3fce2c2715', 'db4fd70a-2d37-40ea-873f-9433dc5e301f', '853c8244-429e-46ca-89f2-addf40dfb2bd', '7a4a336d-dcfa-45a0-b014-824c7619e8de']

    gaia_tasks = []
    custom_mcp_servers = [
            # "e2b-server",
            # "terminal-controller",
            "excel",
            "filesystem",
            # "calculator",
            # "ms-playwright",
            # "audio_server",
            # "image_server",
            # "video_server",
            # "search_server",
            # "download_server",
            # "document_server",
            # "youtube_server",
            # "reasoning_server",
            "e2b-code-server",
            "google-search"
        ]

    for gaia_task_id in gaia_task_ids:
        
        task_id = datetime.now().strftime("%Y%m%d%H%M%S") + "_" + gaia_task_id + "_" + str(uuid.uuid4())
        gaia_tasks.append(
            AworldTask(
                task_id=task_id,
                agent_id="gaia_agent",
                agent_input=gaia_task_id,
                session_id="session_id",
                user_id=os.getenv("USER", "SYSTEM"),
                client_id=get_local_ip(),
                mcp_servers=custom_mcp_servers,
                # llm_model_name="gpt-4o",
                # task_system_prompt=CUSTOM_SYSTEM_PROMPT
            )
        )
    # Run batch processing for questions 1-5
    asyncio.run(_batch_run_gaia_task(gaia_tasks))
