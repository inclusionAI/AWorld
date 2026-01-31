# OceanBase Deployment Guide

This guide helps you set up OceanBase for AWorld.

## Prerequisites

- Docker installed on your system
- Python 3.11+
- PyMySQL or mysql-connector-python

## 1. OceanBase Docker Setup

### Pull and Run OceanBase CE

```bash
# Pull the OceanBase CE image
docker pull oceanbase/oceanbase-ce:latest

# Run OceanBase container
docker run --name oceanbase -e MODE=mini -e OB_SERVER_IP=127.0.0.1 \
  -p 2881:2881 -d oceanbase/oceanbase-ce:latest
```

### Verify Connection

Wait for OceanBase to initialize (about 30 seconds), then connect using the MySQL client:

```bash
# Connect to OceanBase
mysql -h127.0.0.1 -P2881 -uroot@sys
```

Default connection parameters:
- Host: `127.0.0.1`
- Port: `2881`
- User: `root@sys`
- Password: (empty)

### Check OceanBase Status

```sql
-- Check cluster status
SHOW PARAMETERS LIKE 'zone';
```

## 2. Database Initialization

Connect to OceanBase and execute the following SQL commands:

```sql
-- Create database for AWorld
CREATE DATABASE IF NOT EXISTS aworld_db;
USE aworld_db;

-- Memory items table
CREATE TABLE IF NOT EXISTS aworld_memory_items (
    id VARCHAR(255) PRIMARY KEY,
    content TEXT NOT NULL,
    created_at VARCHAR(255) NOT NULL,
    updated_at VARCHAR(255) NOT NULL,
    memory_meta TEXT NOT NULL,
    tags TEXT NOT NULL,
    memory_type VARCHAR(50) NOT NULL,
    version INT NOT NULL DEFAULT 1,
    deleted TINYINT(1) NOT NULL DEFAULT 0
);

-- Memory histories table
CREATE TABLE IF NOT EXISTS aworld_memory_histories (
    memory_id VARCHAR(255) NOT NULL,
    history_id VARCHAR(255) NOT NULL,
    created_at VARCHAR(255) NOT NULL,
    PRIMARY KEY (memory_id, history_id),
    FOREIGN KEY (memory_id) REFERENCES aworld_memory_items (id),
    FOREIGN KEY (history_id) REFERENCES aworld_memory_items (id)
);

-- Performance indexes
CREATE INDEX idx_memory_items_type ON aworld_memory_items (memory_type);
CREATE INDEX idx_memory_items_created ON aworld_memory_items (created_at);
CREATE INDEX idx_memory_items_deleted ON aworld_memory_items (deleted);
```

### Verify Tables

```sql
-- List created tables
SHOW TABLES;

-- Describe the memory_items table
DESCRIBE aworld_memory_items;
```

## 3. Python Connection Test

Create a test script to verify the connection:

```python
#!/usr/bin/env python3
"""
OceanBase connection test script for AWorld.
"""

import pymysql

# OceanBase connection parameters
connection_params = {
    "host": "127.0.0.1",
    "port": 2881,
    "user": "root@sys",
    "password": "",
    "database": "aworld_db",
}


def test_connection():
    """Test OceanBase connection and basic operations."""
    try:
        # Connect to OceanBase
        conn = pymysql.connect(**connection_params)
        cursor = conn.cursor()

        print("Successfully connected to OceanBase!")

        # Insert a test record
        test_id = "test-memory-001"
        test_content = "This is a test memory entry"
        test_meta = '{"source": "test"}'
        test_tags = '["test", "demo"]'

        cursor.execute("""
            INSERT INTO aworld_memory_items
            (id, content, created_at, updated_at, memory_meta, tags, memory_type, version, deleted)
            VALUES (%s, %s, NOW(), NOW(), %s, %s, 'test', 1, 0)
            ON DUPLICATE KEY UPDATE
            content = VALUES(content),
            updated_at = NOW()
        """, (test_id, test_content, test_meta, test_tags,))

        conn.commit()
        print(f"Inserted test record with ID: {test_id}")

        # Verify the insertion
        cursor.execute("SELECT * FROM aworld_memory_items WHERE id = %s", (test_id,))
        result = cursor.fetchone()

        if result:
            print("Verification successful!")
            print(f"  ID: {result[0]}")
            print(f"  Content: {result[1]}")
            print(f"  Type: {result[6]}")

        # Clean up test data
        cursor.execute("DELETE FROM aworld_memory_items WHERE id = %s", (test_id,))
        conn.commit()
        print("Test data cleaned up.")

        cursor.close()
        conn.close()
        print("Connection test completed successfully!")

        return True

    except pymysql.Error as e:
        print(f"Database error: {e}")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


if __name__ == "__main__":
    test_connection()
```

### Run the Test

```bash
# Install PyMySQL
pip install pymysql

# Run the test script
python test_oceanbase_connection.py
```

Expected output:
```
Successfully connected to OceanBase!
Inserted test record with ID: test-memory-001
Verification successful!
  ID: test-memory-001
  Content: This is a test memory entry
  Type: test
Test data cleaned up.
Connection test completed successfully!
```

## 4. AWorld Configuration

Update your AWorld configuration to use OceanBase:

```python
# config.py or environment variables
DATABASE_CONFIG = {
    "host": "127.0.0.1",
    "port": 2881,
    "user": "root@sys",
    "password": "",
    "database": "aworld_db",
}
```

## 5. Troubleshooting

### Connection Refused

If you get a connection refused error:
1. Verify OceanBase container is running: `docker ps | grep oceanbase`
2. Check container logs: `docker logs oceanbase`
3. Wait 30 seconds for initialization

### Access Denied

If you get an access denied error:
1. Ensure the user has correct permissions
2. Try connecting without password first

### Table Creation Fails

If table creation fails:
1. Verify the database exists: `SHOW DATABASES`
2. Use the correct database: `USE aworld_db`

## Related Documentation

- [OceanBase Documentation](https://oceanbase.github.io/docs/)
- [AWorld Memory System](../aworld/memory/README.md)
