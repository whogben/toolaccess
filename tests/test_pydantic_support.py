"""Tests for Pydantic model integration."""

import json
from typing import Optional

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from typer.testing import CliRunner

from toolaccess import (
    CLIServer,
    InvocationContext,
    OpenAPIServer,
    ServerManager,
    SurfaceSpec,
    ToolDefinition,
    ToolService,
)
from toolaccess.codecs import PydanticModelCodec
from toolaccess.definition import (
    get_pydantic_model_params,
    is_pydantic_model,
)


@pytest.fixture
def mock_ctx():
    return InvocationContext(surface="rest")


@pytest.fixture
def runner():
    return CliRunner()


class TestPydanticModelCodec:
    def test_decode_json_string_to_pydantic_model(self, mock_ctx):
        class TestModel(BaseModel):
            name: str
            value: int

        codec = PydanticModelCodec(TestModel)
        json_str = '{"name": "test", "value": 42}'
        result = codec.decode(json_str, parameter_name="data", ctx=mock_ctx)
        assert isinstance(result, TestModel)
        assert result.name == "test"
        assert result.value == 42

    def test_decode_dict_to_pydantic_model(self, mock_ctx):
        class TestModel(BaseModel):
            name: str
            value: int

        codec = PydanticModelCodec(TestModel)
        data = {"name": "test", "value": 42}
        result = codec.decode(data, parameter_name="data", ctx=mock_ctx)
        assert isinstance(result, TestModel)
        assert result.name == "test"
        assert result.value == 42

    def test_passes_through_already_decoded_model(self, mock_ctx):
        class TestModel(BaseModel):
            name: str
            value: int

        codec = PydanticModelCodec(TestModel)
        instance = TestModel(name="test", value=42)
        result = codec.decode(instance, parameter_name="data", ctx=mock_ctx)
        assert result is instance

    def test_handles_none(self, mock_ctx):
        class TestModel(BaseModel):
            name: str
            value: int

        codec = PydanticModelCodec(TestModel)
        result = codec.decode(None, parameter_name="data", ctx=mock_ctx)
        assert result is None

    def test_raises_value_error_for_invalid_json(self, mock_ctx):
        class TestModel(BaseModel):
            name: str
            value: int

        codec = PydanticModelCodec(TestModel)
        with pytest.raises(ValueError):
            codec.decode("not valid json", parameter_name="data", ctx=mock_ctx)


class TestIsPydanticModel:
    def test_returns_true_for_pydantic_base_model_subclass(self):
        class TestModel(BaseModel):
            name: str
            value: int

        assert is_pydantic_model(TestModel) is True

    def test_returns_false_for_regular_types(self):
        assert is_pydantic_model(str) is False
        assert is_pydantic_model(int) is False
        assert is_pydantic_model(dict) is False
        assert is_pydantic_model(list) is False

    def test_returns_false_for_pydantic_model_instance(self, mock_ctx):
        class TestModel(BaseModel):
            name: str

        instance = TestModel(name="test")
        assert is_pydantic_model(instance) is False


class TestGetPydanticModelParams:
    def test_correctly_identifies_pydantic_model_parameters(self):
        class UserModel(BaseModel):
            name: str
            email: str

        class ConfigModel(BaseModel):
            timeout: int
            debug: bool

        def process_user(user: UserModel, config: ConfigModel, name: str) -> str:
            return name

        result = get_pydantic_model_params(process_user)
        assert result == {"user": UserModel, "config": ConfigModel}

    def test_returns_empty_dict_when_no_pydantic_params(self):
        def simple_func(a: int, b: str) -> str:
            return f"{a}-{b}"

        result = get_pydantic_model_params(simple_func)
        assert result == {}


class TestToolWithPydanticParamRest:
    def test_tool_with_pydantic_param_via_rest(self):
        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str
            age: int

        def create_user(user: UserInput) -> dict:
            return {"created": True, "name": user.name, "age": user.age}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=create_user, name="create_user")],
        )
        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)
        response = client.post("/api/create_user", json={"name": "John", "age": 30})
        assert response.status_code == 200
        result = response.json()
        assert result["created"] is True
        assert result["name"] == "John"
        assert result["age"] == 30


class TestOpenAPISchemaWithPydanticModel:
    def test_openapi_spec_contains_pydantic_model_schema(self):
        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str = Field(description="The user's full name")
            age: int = Field(description="The user's age in years")

        def create_user(user: UserInput) -> dict:
            return {"created": True}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=create_user, name="create_user")],
        )
        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)
        spec = client.get("/api/openapi.json").json()

        schema = spec["components"]["schemas"].get("UserInput")
        assert schema is not None
        assert "properties" in schema
        assert "name" in schema["properties"]
        assert "age" in schema["properties"]

        name_schema = schema["properties"]["name"]
        assert name_schema["type"] == "string"
        assert "The user's full name" in str(name_schema.get("description", ""))


class TestToolWithPydanticParamCli:
    def test_tool_with_pydantic_param_via_cli(self, runner):
        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str
            age: int

        def create_user(user: UserInput) -> dict:
            return {"created": True, "name": user.name, "age": user.age}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=create_user, name="create_user")],
        )

        cli = CLIServer("tools")
        cli.mount(svc)
        mgr.add_server(cli)

        result = runner.invoke(
            mgr.cli, ["tools", "create_user", '{"name": "Alice", "age": 25}']
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert parsed["created"] is True
        assert parsed["name"] == "Alice"
        assert parsed["age"] == 25


class TestOptionalPydanticParam:
    def test_tool_with_optional_pydantic_param_via_rest(self):
        """Test Optional[pydantic model] parameter via REST server."""

        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str
            age: int

        def update_user(user: Optional[UserInput] = None) -> dict:
            if user is None:
                return {"updated": False}
            return {"updated": True, "name": user.name, "age": user.age}

        svc = ToolService(
            "tools",
            [
                ToolDefinition(
                    func=update_user,
                    name="update_user",
                    surfaces={"rest": SurfaceSpec(http_method="POST")},
                )
            ],
        )

        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)

        response = client.post("/api/update_user", json={"name": "Bob", "age": 30})
        assert response.status_code == 200
        data = response.json()
        assert data["updated"] is True
        assert data["name"] == "Bob"
        assert data["age"] == 30

    def test_tool_with_optional_pydantic_param_via_rest_null(self):
        """Test Optional[pydantic model] parameter with null value via REST."""

        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str

        def update_user(user: Optional[UserInput] = None) -> dict:
            if user is None:
                return {"updated": False}
            return {"updated": True, "name": user.name}

        svc = ToolService(
            "tools",
            [
                ToolDefinition(
                    func=update_user,
                    name="update_user",
                    surfaces={"rest": SurfaceSpec(http_method="POST")},
                )
            ],
        )

        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)

        response = client.post("/api/update_user", json=None)
        assert response.status_code == 200
        data = response.json()
        assert data["updated"] is False

    def test_tool_with_optional_pydantic_param_via_cli(self, runner):
        """Test Optional[pydantic model] parameter via CLI server."""

        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str
            age: int

        def update_user(user: UserInput) -> dict:
            return {"updated": True, "name": user.name, "age": user.age}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=update_user, name="update_user")],
        )

        cli = CLIServer("tools")
        cli.mount(svc)
        mgr.add_server(cli)

        result = runner.invoke(
            mgr.cli, ["tools", "update_user", '{"name": "Charlie", "age": 35}']
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert parsed["updated"] is True
        assert parsed["name"] == "Charlie"
        assert parsed["age"] == 35

    def test_tool_with_optional_pydantic_param_via_cli_null(self, runner):
        """Test Optional[pydantic model] parameter with null via CLI."""

        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str

        def update_user(user: Optional[UserInput] = None) -> dict:
            if user is None:
                return {"updated": False}
            return {"updated": True, "name": user.name}

        svc = ToolService(
            "tools",
            [ToolDefinition(func=update_user, name="update_user")],
        )

        cli = CLIServer("tools")
        cli.mount(svc)
        mgr.add_server(cli)

        result = runner.invoke(mgr.cli, ["tools", "update_user"])
        assert result.exit_code == 0
        parsed = json.loads(result.output.strip())
        assert parsed["updated"] is False

    def test_tool_with_optional_pydantic_param_openapi_schema(self):
        """Test that Optional[pydantic model] shows in OpenAPI schema."""

        mgr = ServerManager("test")

        class UserInput(BaseModel):
            name: str = Field(description="The user's name")
            age: int = Field(description="The user's age")

        def update_user(user: Optional[UserInput] = None) -> dict:
            return {"updated": user is not None}

        svc = ToolService(
            "tools",
            [
                ToolDefinition(
                    func=update_user,
                    name="update_user",
                    surfaces={"rest": SurfaceSpec(http_method="POST")},
                )
            ],
        )

        api = OpenAPIServer("/api", "API")
        api.mount(svc)
        mgr.add_server(api)

        client = TestClient(mgr.app)
        spec = client.get("/api/openapi.json").json()

        schema = spec["components"]["schemas"].get("UserInput")
        assert schema is not None
        assert "properties" in schema
        assert schema["properties"]["name"]["description"] == "The user's name"

    def test_get_pydantic_model_params_with_optional(self):
        """Test get_pydantic_model_params detects Optional pydantic models."""

        class UserInput(BaseModel):
            name: str
            age: int

        def update_user(user: Optional[UserInput] = None) -> dict:
            pass

        params = get_pydantic_model_params(update_user)
        assert "user" in params
        assert params["user"] is UserInput

    def test_is_pydantic_model_with_optional(self):
        """Test is_pydantic_model detects Optional pydantic models."""

        class UserInput(BaseModel):
            name: str

        assert is_pydantic_model(Optional[UserInput]) is True
        assert is_pydantic_model(UserInput) is True
        assert is_pydantic_model(Optional[str]) is False
