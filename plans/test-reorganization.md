# Test File Reorganization Plan

## Current State

- **`tests/test_toolaccess_features.py`**: 1,743 lines (too large)
- **`tests/test_toolaccess.py`**: 334 lines (integration tests)

## Problem

The `test_toolaccess_features.py` file has grown too large, making it difficult to:
- Navigate and find specific tests
- Maintain and update tests
- Understand test coverage at a glance
- Run focused test suites

## Proposed Reorganization

### File Structure

```
tests/
├── test_toolaccess.py                    # Keep existing (334 lines)
├── test_context.py                       # NEW (~135 lines)
├── test_codecs.py                        # NEW (~180 lines)
├── test_renderers.py                     # NEW (~100 lines)
├── test_context_injection.py             # NEW (~65 lines)
├── test_pipeline.py                      # NEW (~355 lines)
├── test_integration.py                   # NEW (~390 lines)
├── test_edge_cases.py                    # NEW (~70 lines)
└── test_pydantic_support.py              # NEW (~380 lines)
```

### File Breakdown

#### 1. `test_context.py` (~135 lines)
**Source**: Lines 61-195 from `test_toolaccess_features.py`

**Test Classes**:
- `TestInvocationContext` - Context creation and field handling
- `TestPrincipal` - Principal object creation and properties
- `TestAccessPolicy` - Access policy configuration
- `TestSurfaceSpec` - Surface specification configuration

**Purpose**: Tests for core context and authorization primitives

---

#### 2. `test_codecs.py` (~180 lines)
**Source**: Lines 197-375 from `test_toolaccess_features.py`

**Test Classes**:
- `TestIdentityCodec` - Pass-through codec
- `TestJsonObjectCodec` - JSON string to dict codec
- `TestJsonValueCodec` - JSON string to any value codec
- `TestCsvListCodec` - CSV string to list codec
- `TestBase64BytesCodec` - Base64 string to bytes codec

**Purpose**: Tests for all codec implementations

---

#### 3. `test_renderers.py` (~100 lines)
**Source**: Lines 377-477 from `test_toolaccess_features.py`

**Test Classes**:
- `TestNoOpRenderer` - No-op renderer
- `TestJsonRenderer` - JSON renderer with formatting options
- `TestPydanticJsonRenderer` - Pydantic-aware JSON renderer

**Purpose**: Tests for all renderer implementations

---

#### 4. `test_context_injection.py` (~65 lines)
**Source**: Lines 479-545 from `test_toolaccess_features.py`

**Test Classes**:
- `TestGetContextParam` - Context parameter detection
- `TestInjectContext` - Context injection marker

**Purpose**: Tests for context injection mechanism

---

#### 5. `test_pipeline.py` (~355 lines)
**Source**: Lines 547-902 from `test_toolaccess_features.py`

**Test Classes**:
- `TestResolvePrincipal` - Principal resolution logic
- `TestValidateAccess` - Access policy validation
- `TestDecodeArgs` - Argument decoding with codecs
- `TestCallUserFunc` - User function invocation
- `TestRenderResult` - Result rendering with priority
- `TestInvokeTool` - Full pipeline integration

**Purpose**: Tests for the complete tool invocation pipeline

---

#### 6. `test_integration.py` (~390 lines)
**Source**: Lines 904-1292 from `test_toolaccess_features.py`

**Test Classes**:
- `TestDecoratorAPI` - ToolService decorator API
- `TestToolWithAccessPolicy` - Access policy enforcement
- `TestToolWithCodecs` - Codec registration and application
- `TestToolWithRenderer` - Custom renderer usage
- `TestPublicSignatureHelpers` - Public signature extraction
- `TestRestServerWithContextInjection` - REST server context injection
- `TestCliServerWithRenderer` - CLI server rendering
- `TestMcpServerWithPublicSignatures` - MCP server signature handling

**Purpose**: Integration tests across servers and surfaces

---

#### 7. `test_edge_cases.py` (~70 lines)
**Source**: Lines 1294-1361 from `test_toolaccess_features.py`

**Test Classes**:
- `TestEdgeCases` - Various edge cases and corner scenarios

**Purpose**: Tests for edge cases and unusual scenarios

---

#### 8. `test_pydantic_support.py` (~380 lines)
**Source**: Lines 1363-1742 from `test_toolaccess_features.py`

**Test Classes**:
- `TestPydanticModelCodec` - Pydantic model codec
- `TestIsPydanticModel` - Pydantic model detection
- `TestGetPydanticModelParams` - Pydantic parameter extraction
- `TestToolWithPydanticParamRest` - REST server with Pydantic params
- `TestOpenAPISchemaWithPydanticModel` - OpenAPI schema generation
- `TestToolWithPydanticParamCli` - CLI server with Pydantic params
- `TestOptionalPydanticParam` - Optional Pydantic parameters

**Purpose**: Tests for Pydantic model integration

---

## Migration Strategy

### Phase 1: Create New Test Files
1. Create each new test file with appropriate imports
2. Copy test classes from `test_toolaccess_features.py`
3. Update imports as needed
4. Verify each file runs independently

### Phase 2: Verify Test Coverage
1. Run all tests to ensure no regressions
2. Verify test discovery works correctly
3. Check that pytest can run individual test files
4. Confirm test counts match before/after

### Phase 3: Cleanup
1. Delete `test_toolaccess_features.py`
2. Update any documentation references
3. Update CI/CD configurations if needed

## Benefits

1. **Improved Navigation**: Easier to find specific tests
2. **Better Maintainability**: Smaller files are easier to update
3. **Focused Testing**: Can run specific test suites (e.g., `pytest tests/test_codecs.py`)
4. **Clearer Organization**: Logical grouping by functionality
5. **Scalability**: New tests can be added to appropriate files

## Considerations

- All new files will share common fixtures (e.g., `mock_ctx`, `runner`)
- Consider creating a `conftest.py` for shared fixtures if needed
- Maintain consistent import structure across files
- Keep test naming conventions consistent

## Test Count Summary

| File | Approx. Lines | Test Classes |
|------|--------------|--------------|
| test_context.py | 135 | 4 |
| test_codecs.py | 180 | 5 |
| test_renderers.py | 100 | 3 |
| test_context_injection.py | 65 | 2 |
| test_pipeline.py | 355 | 6 |
| test_integration.py | 390 | 8 |
| test_edge_cases.py | 70 | 1 |
| test_pydantic_support.py | 380 | 7 |
| **Total** | **1,675** | **36** |

Note: Line counts are estimates based on current file structure.
