# FLARE AI assistant

`flare_ai` is the optional, removable Django application for FLARE's configurable assistant. It owns
the agent, validated tools, streamed chat interface, access control, static assets
and tests. The ordinary `lab` application must never import it.

## Enable the assistant

The app is present in the standard image but disabled by default. Configure:

```text
FLARE_AI_ENABLED=1
FLARE_AI_BASE_URL=http://model-server:1234/v1
FLARE_AI_API_KEY=provider-key
FLARE_AI_MODEL=model-identifier
FLARE_AI_GROUP_NAME=FLARE AI users
FLARE_AI_ASSISTANT_NAME=Marcott-E
```

Run the project migrations, create the configured group in Django Admin, and add
approved users to it. Group membership is strict: staff and superusers must also
belong to the group. Disabled users and non-members do not receive the drawer
and cannot call its endpoints.

Provider configuration is deployment-owned in this release. Secrets must stay
in the deployment `.env`, never in source or browser-visible settings.
`FLARE_AI_ASSISTANT_NAME` controls both the displayed chat name and the identity
given to the model; restart the web process after changing it.

## Chat behavior

The authenticated drawer is mounted from the shared FLARE base template. Its
transcript, open state and conversation UUID live in per-tab `sessionStorage`;
authoritative Pydantic message history is bounded to ten completed turns in the
existing Django session. Responses stream as newline-delimited JSON from
`/ai/chat/stream/`. Navigating during a response interrupts that response while
preserving completed messages.

Opening an empty chat sends a hidden greeting to warm the model while the drawer
shows a loading indicator. The completed greeting offers three starter
questions. Reset starts a new conversation and repeats this warm-up.

Only the relative current path is submitted. The server resolves the route and
related record identifiers/names; raw page HTML and unsaved form values are not
sent to the model.

The first explicit user prompt each day creates one `AI assistant used` entry in
FLARE's existing read-only Usage Events admin report. Further prompts from that
user on the same day do not create more entries. The event contains the user,
timestamp and a count of one; it never contains the prompt, response, page
context or conversation ID. Hidden warm-up greetings are not counted.

## File attachments

A chat prompt may carry a single attached file. The client sends it as
`multipart/form-data` with the fields `conversation_id`, `page_path`, `message`
and `attachment`; the browser sets the multipart `Content-Type` boundary, so the
client must not set it manually. JSON prompts without a file keep using the
existing `application/json` body and remain fully supported.

Supported files are CSV, XLSX, TXT and Markdown, up to 10 MB. The server
validates the extension, rejects empty and oversized files, accepts exactly one
file, and refuses to pair an attachment with the hidden greeting. All validation
happens before the agent runs, so a rejected upload never calls the model and
never records usage.

The upload is request-scoped. `flare_ai.attachments` exposes only safe
metadata (identifier, name, lower-cased suffix, size, MIME type and the Django
uploaded file) through `AIRequestContext.attachment`; it never reads or copies
the file content. The server closes the underlying file in a `finally` block, so
it is released on success, provider failure and interrupted streaming. Nothing is
written to disk and no database row is created for the file.

In this release the assistant cannot inspect the attached content. It receives
the metadata only and must say so explicitly rather than inventing an analysis.
The browser keeps the selected `File` in memory for the life of the open drawer
and resends it with follow-up prompts; closing the drawer, removing it, or
navigating discards it. Because `File` objects cannot be stored in
`sessionStorage`, the attachment is never persisted between sessions.

## Tools

- `search_mice(filters: MouseSearchInput)` performs bounded, typed animal
  searches and returns relative record and combined-summary URLs.
- `search_recordings(filters: RecordingSearchInput)` performs bounded, typed
  recording searches and returns selected-recording and experiment URLs.
- `user_guide(topic="overview")` returns verified static website instructions.
- `prepare_animal_form(draft: AnimalFormDraftInput)` validates a review-only
  Add animals payload, stores it temporarily in the user's session and returns
  a relative review URL. It never submits the form or creates animals.

All ORM lookup paths are defined by server code. The model supplies validated
values and never supplies SQL or arbitrary Django lookup expressions. Search
tools are read-only; the animal-form tool can write only a short-lived session
draft, while the existing Django form remains the sole record-creation path.

Direct tool example:

```python
from flare_ai.schemas import MouseSearchInput
from flare_ai.tools.mice import search_mice

print(search_mice(MouseSearchInput(status="Alive", sex="F")))
```

## Tests

```sh
.venv_ai/bin/python manage.py test flare_ai.tests --settings=labdb.test_settings
```

The tests use fake models for the chat transport and do not contact a live AI
provider. Update `user_guide.md` and its topic literal together when FLARE
workflows change.
