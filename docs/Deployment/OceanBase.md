# OceanBase Deployment Guide

This guide explains how to deploy [OceanBase Database](https://github.com/oceanbase/oceanbase) (Community Edition) for use with AWorld. OceanBase is a distributed SQL database that offers high availability and scalability, compatible with MySQL protocol.

## Prerequisites

- **Docker**: Ensure Docker is installed and running.
- **Resources**: Recommended at least 4 CPU cores and 8GB RAM for the container.

## 1. Deploy with Docker

The fastest way to get started is using the official standalone Docker image.

Run the following command to start a mini standalone instance:

```bash
docker run -p 2881:2881 --name oceanbase-ce -e MODE=mini -e OB_root_PASSWORD=root_password -d oceanbase/oceanbase-ce:latest
```

*   `MODE=mini`: Optimized for low-resource development environments.
*   `-p 2881:2881`: Maps the SQL port.

Wait for the initialization to complete (typically 2-5 minutes). You can check the status with:

```bash
docker logs -f oceanbase-ce
```

Look for the message `boot success!`.

## 2. Database Initialization

Once the container is running, you need to create a database for AWorld.

Connect to the instance using a MySQL client:

```bash
# Connect using the root user (default tenant is usually 'test' or 'sys' depending on version, try root@test)
mysql -h127.0.0.1 -P2881 -uroot@test -proot_password
```

Create the database and schema:

```sql
CREATE DATABASE IF NOT EXISTS aworld_db;
USE aworld_db;

-- (Optional) If AWorld requires specific tables, apply them here.
-- Example:
-- CREATE TABLE IF NOT EXISTS test_table (id INT PRIMARY KEY, name VARCHAR(255));
```

## 3. Integration with AWorld

Configure AWorld to use OceanBase as its storage backend. Update your configuration file (e.g., `config.yaml` or `.env`) with the following parameters:

```yaml
database:
  type: oceanbase
  host: 127.0.0.1
  port: 2881
  user: root@test
  password: root_password
  database: aworld_db
```

> **Note**: OceanBase usernames often follow the format `user@tenant`. For the standalone Docker image, the default tenant is usually `test`.

## 4. Verification Script

You can verify the connectivity and compatibility using the following Python script:

```python
import pymysql
import sys

def verify_connection():
    config = {
        'host': '127.0.0.1',
        'port': 2881,
        'user': 'root@test',
        'password': 'root_password',
        'database': 'aworld_db',
        'charset': 'utf8mb4',
        'cursorclass': pymysql.cursors.DictCursor
    }

    try:
        print(f"Connecting to OceanBase at {config['host']}:{config['port']}...")
        connection = pymysql.connect(**config)

        with connection.cursor() as cursor:
            # Check version
            cursor.execute("SELECT VERSION()")
            version = cursor.fetchone()
            print(f"✅ Connection successful! Server Version: {version['VERSION()']}")

            # Create a test table
            cursor.execute("CREATE TABLE IF NOT EXISTS ob_connectivity_test (id INT PRIMARY KEY, msg VARCHAR(50))")
            cursor.execute("INSERT INTO ob_connectivity_test VALUES (1, 'Hello OceanBase') ON DUPLICATE KEY UPDATE msg='Hello OceanBase'")
            connection.commit()
            print("✅ Write test successful!")

            # Read back
            cursor.execute("SELECT * FROM ob_connectivity_test WHERE id=1")
            result = cursor.fetchone()
            print(f"✅ Read test successful! Data: {result}")

            # Cleanup
            cursor.execute("DROP TABLE ob_connectivity_test")
            connection.commit()

    except Exception as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)
    finally:
        if 'connection' in locals() and connection.open:
            connection.close()

if __name__ == "__main__":
    verify_connection()
```

## 5. Troubleshooting

*   **Connection Refused**: Ensure the Docker container is running (`docker ps`) and port 2881 is mapped correctly.
*   **Authentication Failed**: Double-check the tenant name in the username (`root@test` vs `root`). The standalone image default is often `test`.
*   **Resource Issues**: If the container exits unexpectedly, check if Docker has enough memory allocated (minimum 6GB recommended for stability).
