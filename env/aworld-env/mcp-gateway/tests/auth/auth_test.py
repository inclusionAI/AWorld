import time
import jwt


def gen_auth_token(root_token: str, app: str):
    pay_load = {"app": app, "version": 1, "time": time.time()}
    token = jwt.encode(payload=pay_load, key=root_token, algorithm="HS256")
    return token


def test_gen_token(app: str, root_token: str):
    token = gen_auth_token(root_token, app)
    print(f"Token: {token}")


if __name__ == "__main__":
    import os

    root_token = os.getenv("MCP_GATEWAY_TOKEN_SECRET", "123321")
    test_gen_token("test_client", root_token)
