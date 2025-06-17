import os


def list_agents():
    agents_dir = os.path.join(os.getcwd(), "agent_deploy")

    if not os.path.exists(agents_dir):
        return []

    try:
        # 列出agents_dir下的所有目录
        agents = []
        for item in os.listdir(agents_dir):
            item_path = os.path.join(agents_dir, item)
            if os.path.isdir(item_path):
                # 检查是否包含agent.py文件
                agent_file = os.path.join(item_path, "agent.py")
                if os.path.exists(agent_file):
                    agents.append(item)
        return agents
    except OSError as e:
        # 处理权限错误或其他文件系统错误
        print(f"Error listing agents: {e}")
        return []


def get_agent_package_path(agent_name):
    return os.path.join(
        os.getcwd(),
        "agent_deploy",
        agent_name,
    )
