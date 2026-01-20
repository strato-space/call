# MCP MediaGen

## 1. Goal & Scope
- [ ] Provide **two OpenAI-based image generation tools**:
  - [x] `image_generation_gpt_image` - wrapper around the OpenAI Images API `gpt-image-1`.
  - [ ] `image_generation_gpt` - wrapper around the OpenAI Responses API `gpt-5.*` with the built-in `image_generation` tool.
- [x] Accept a text `prompt`, call the corresponding OpenAI endpoint, and store all binary results on the filesystem.
- [x] Each generated file is stored with a unique GUID filename in a single directory `MEDIA_GEN_MCP_OUTPUT_DIR`.
- [ ] The tool returns only:
  - [ ] the model's textual response (or a list of textual responses),
  - [ ] a one-dimensional array of either local paths (`paths: [...]`) or public URLs (`urls: [...]`) - the format is selected by a simple switch.

## 2. Public Contract (Inputs / Outputs / Config)

### 2.1 Inputs: `image_generation_gpt_image` (OpenAI Images API, `gpt-image-1`)

- `model: "gpt-image-1"` - image model selection.
- `prompt: string` - text description for generation.
- `n: integer` - number of images.
- `size: "1024x1024" | "1024x1536" | "1536x1024"` - image size.
- `quality: "low" | "medium" | "high"` - render quality.
- `background: "auto" | "transparent" | "opaque"` - background mode.
- `output_mode: "paths" | "urls"` - local tool parameter; controls whether it returns `paths: [...]` (absolute paths) or `urls: [...]`. It is **not** sent to the OpenAI Images API.
- `mode: "compact" | "filtered" | "full"` - local tool parameter that defines the textual output shape (see 2.3). It is **not** sent to the OpenAI Images API.

### 2.2 Inputs: `image_generation_gpt` (OpenAI Responses API, `gpt-5.*` + `image_generation`)

- `model: "gpt-5" | "gpt-5.1" | ...` - text/multimodal model selection.
- `input` / `messages` - text/messages describing the request and image requirements.
- `tools: [{"type": "image_generation"}, ...]` - tool list that includes the built-in `image_generation`.
- `output_mode: "paths" | "urls"` - local tool parameter; controls the response format (`paths: [...]` or `urls: [...]`). It is not sent to the OpenAI Responses API (only `model`/`input`/`tools`) and is used only in our runtime.
- `mode: "compact" | "filtered" | "full"` - local tool parameter; controls how text/structure is returned (see below). It is not sent to the OpenAI Responses API.

### 2.3 Output: `ImageDeliveryResult`

What the caller sees (REST/MCP/CLI):

- `paths?: list[str]` - if `output_mode="paths"`, array of absolute paths in `MEDIA_GEN_MCP_OUTPUT_DIR`.
- `urls?: list[str]` - if `output_mode="urls"`, array of public URLs built from `MEDIA_GEN_MCP_URL_PREFIX`.
- `output_text?: str` - human-readable text **as returned by the model** (Responses API);
  - the image tool does not add or rephrase text,
  - only mechanical replacement of inline base64/data-URL/temp links with final `paths`/`urls` is allowed in the response.

The `mode` affects which additional fields (besides `paths`/`urls` and `output_text`) are returned in the envelope:

- `"compact"` (default) - minimal mode:
  - returns only `ImageDeliveryResult` (as described above);
  - the full raw LLM response is **not** returned.
- `"full"` - full LLM response:
  - adds `llm_response_raw` with the original LLM response (Responses API) **without** modifications;
  - `output_text`, if present, may duplicate the main text from this response.
- `"filtered"` - full LLM response with binaries removed:
  - adds `llm_response_filtered` with the same structure as the raw LLM response,
  - in the entire object **all binary fields** (base64/data-URL/temp links/embedded blobs) are removed and replaced with string values pointing to `paths` or `urls`;
  - `output_text`, if present, follows the same rule (only mechanical link replacement).

### 2.4 Environment settings (filesystem + URL)

- [x] `MEDIA_GEN_MCP_OUTPUT_DIR` - base directory for all generated files; `paths` are stored here.
- [x] `MEDIA_GEN_MCP_URL_PREFIX` - optional URL prefix (e.g., `"https://media-gen.example.com/static"`), used only when URLs are requested.

All files are always stored in `MEDIA_GEN_MCP_OUTPUT_DIR` with filenames like `"<guid>.<ext>"` (flat storage). The only difference is whether the response returns a list of paths or URLs, determined by `output_mode` for the specific tool (`image_generation_gpt_image` or `image_generation_gpt`).

## 3. Core Modules Layout (call/image_tool/*)

### 3.1 image_tool/common.py

- Artifact definitions for backends: `ImageArtifact`, `ImageDeliveryResult`.
- Validation for parameters that are sent to OpenAI:
  - validate `size`, `n`, `quality`, `background` for `gpt-image-1`.

### 3.2 image_tool/storage.py

- Work with `MEDIA_GEN_MCP_OUTPUT_DIR`:
  - `ensure_output_dir(MEDIA_GEN_MCP_OUTPUT_DIR: Path) -> Path` - creates the directory if missing.
- GUID filename generation: `"<uuid4>.<ext>"`.
- Write backend artifacts directly to files in `MEDIA_GEN_MCP_OUTPUT_DIR` and return final paths.

### 3.3 image_tool/delivery.py

- Function `deliver_artifacts(artifacts: list[ImageArtifact], output_mode: Literal["paths", "urls"]) -> ImageDeliveryResult`.
- Logic:
  - for each `ImageArtifact`, choose file extension and generate a GUID filename;
  - save contents to `MEDIA_GEN_MCP_OUTPUT_DIR` via `storage`;
  - if `output_mode="paths"` - return `paths` with absolute paths;
  - if `output_mode="urls"` - return `urls` built as `MEDIA_GEN_MCP_URL_PREFIX + <guid> + <ext>`;
  - build `ImageDeliveryResult` without extra text (except `output_text` from the model).

### 3.4 image_tool/backends/responses_backend.py

- Function `generate_with_responses(request: ImageGenerationRequest, client: OpenAI) -> list[ImageArtifact]`.
- Uses **Responses API**:
  - `client.responses.create()` or a specialized image endpoint, including `model="gpt-5.*"`;
  - collects `b64_json` / file ids into `ImageArtifact` and saves directly to `MEDIA_GEN_MCP_OUTPUT_DIR` via `storage`.

## 4. Subsystem Integration

### 4.1 call.lib

- Add a high-level function `generate_image_api(...) -> dict`:
  - implemented in `call.lib.api` and wraps the core modules (`image_tool/*`).
  - accepts parameters described in sections 2.2/2.3 (`prompt`, OpenAI fields for the selected backend, `output_mode`).
  - maps exceptions into the standard envelope (`ok`, `error`, `error_code`, `description`, `attributes.images`).
- Update/extend `RunnableConfig.tools`:
  - recognize tool names: `image_generation_gpt` and `image_generation_gpt_image`.

### 4.2 app/call.py

- Register new tool factories in `get_tool_by_name`:
  - `"image_generation_gpt"` -> wrapper around Responses API (`gpt-5.*` + `image_generation`).
  - `"image_generation_gpt_image"` -> wrapper around Images API (`gpt-image-1`).

### 4.3 actions/main.py

- Add endpoint `/images`:
  - `POST /images` with JSON payload:
    - `tool: "image_generation_gpt" | "image_generation_gpt_image"` - backend selection.
    - `prompt: str`.
    - optional image fields (`size`, `n`, `quality`, `background`) - only for `image_generation_gpt_image`.
    - `output_mode: "paths" | "urls"`.
  - Endpoint calls `generate_image_api` and returns the standard envelope with `attributes.images` (see 2.4).

### 4.4 mcp/server.py and mcp_config.yaml

- In the `call` MCP server, add tool `generate_image` that accepts the same JSON as `/images` and proxies to `generate_image_api`.
- In `mcp_config.yaml` under `call:`, add `generate_image` to the tool list with a short intent description ("image generation and save to FS/URL").

### 4.5 Integration points in current code

- **`call.lib.api`**
  - `RunnableConfig` and `build_runnable_instructions_config()` - build the tool list including `image_generation_gpt` / `image_generation_gpt_image`.
  - `call()` / `call_async()` / `api_interpret_exec_payload()` - external entrypoints (CLI, Actions, MCP, bot) call `generate_image_api` as needed.

- **`call.app.call`**
  - Imports `image_generation_gpt` and `image_generation_gpt_image`, `function_tool`, `RunContextWrapper`.
  - `get_tool_by_name()` - returns the appropriate class/function by name.

- **`actions/main.py`**
  - `/call` and `/exec` continue to call `api_call` / `api_call_async` and return the standard envelope.
  - `/images` calls `generate_image_api` directly.

- **`mcp/server.py`**
  - Already uses `call.lib.api`; the new MCP tool `generate_image` is just another thin wrapper around `generate_image_api`.

## 6. Safety & Error Handling

- **Filesystem & URL:**
  - All paths are rooted at `MEDIA_GEN_MCP_OUTPUT_DIR`.
  - Absolute paths are allowed only when `allow_custom_dirs=true` and a whitelist of prefixes is provided.
- **Errors:**
  - Any backend/route error must not bubble up as an exception at the MCP/Actions/bot top level;
  - instead, return an envelope with `ok=false`, `error`, `error_code`, `provider_code` (for OpenAI), and a clear `description`.

---

## 7. Testing Strategy

1. **Unit tests for core modules**:
   - validate DTOs, safe path normalization, and GUID filenames.
2. **Backend contract tests** (with mock OpenAI):
   - Images backend (`gpt-image-1`) and Responses backend (`gpt-5.*` + `image_generation`) return consistent `ImageArtifact`/paths for the same prompt under the same settings.
3. **Integration tests**:
   - call `/images` in the Actions API.

## 8. Decisions

1. **Two separate tools instead of a "default backend"**
   - `image_generation_gpt_image` and `image_generation_gpt` are two distinct tools (with different tool names).
   - The question of a default backend does not apply: callers explicitly choose the tool by name.
2. **No centralized artifact catalog**
   - No additional artifact table/registry.
   - For operations, logs and returned paths/URLs (`paths[]` / `urls[]`) are sufficient.
