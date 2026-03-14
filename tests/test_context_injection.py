"""Tests for context injection mechanism."""

from typing import Annotated

from toolaccess import (
    InjectContext,
    InvocationContext,
    get_context_param,
    inject_context,
)


class TestGetContextParam:
    def test_detects_annotated_inject_context(self):
        def func(ctx: Annotated[InvocationContext, InjectContext()]):
            return ctx

        result = get_context_param(func)
        assert result == "ctx"

    def test_detects_plain_invocation_context(self):
        def func(ctx: InvocationContext):
            return ctx

        result = get_context_param(func)
        assert result == "ctx"

    def test_returns_none_when_no_context_param(self):
        def func(a: int, b: str):
            return a + b

        result = get_context_param(func)
        assert result is None

    def test_detects_param_with_different_name(self):
        def func(request_context: Annotated[InvocationContext, InjectContext()]):
            return request_context

        result = get_context_param(func)
        assert result == "request_context"

    def test_ignores_other_annotated_params(self):
        def func(
            a: Annotated[int, "some metadata"],
            ctx: Annotated[InvocationContext, InjectContext()],
        ):
            return a

        result = get_context_param(func)
        assert result == "ctx"

    def test_multiple_params_only_one_context(self):
        def func(
            x: int,
            ctx: InvocationContext,
            y: str,
        ):
            return x

        result = get_context_param(func)
        assert result == "ctx"


class TestInjectContext:
    def test_factory_returns_inject_context(self):
        marker = inject_context()
        assert isinstance(marker, InjectContext)

    def test_inject_context_is_distinct_instances(self):
        m1 = inject_context()
        m2 = inject_context()
        assert isinstance(m1, InjectContext)
        assert isinstance(m2, InjectContext)
