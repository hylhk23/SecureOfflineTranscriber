# The model list (models.json)

Everything Options > Transcription offers for download is described by one file, `models.json`.
The program has a built-in copy in `app\models.json`. To give users new or updated models you send them a
newer `models.json`; nobody needs a new program version unless the model is of a new kind.

## Updating the list — three steps

1. **Edit** `app\models.json` (or a copy). Add, change or remove model entries, or change the Automatic order
   or the recommended set. For a new or changed model you only need to write the repo, the file names and
   `"revision": "main"` — the tool fills in the rest.
2. **Run** `build\tools\update-model-list.bat` (or `py -3 update-model-list.py my-list.json`).
   It looks each model up on huggingface.co, fills in the exact commit, file sizes and SHA-256 checksums,
   sets a new `list_version` (today's date), checks the whole list the same way the program does, and shows
   what is new, updated or removed compared with the program's built-in list. Nothing is written if the
   list has a problem.
   - `--refresh whisper-small` moves one model to the newest commit of its repo; `--refresh-all` does all.
   - `--check` only checks a list.
3. **Send** the file. Users choose Options > Transcription > **Import model list…**, or drop the file on the
   program window. The program checks it again, keeps it as `data\models.json`, and shows what changed.
   **Use built-in list** goes back.

What users then see:

| Change in the list | In the model manager |
|---|---|
| New model | A new row, "Not installed", with Download |
| New revision of a model they have | "Update available" with an Update button; the old files keep working until the new ones are in place |
| Model removed from the list | Their copy stays usable as "*name* (not in the model list)" and can still be deleted |
| New Automatic order or recommended set | Used straight away, unless the user chose their own order in Options |

When a new program version brings a newer built-in list, it replaces an older imported one automatically.

## Top-level fields

| Field | Meaning |
|---|---|
| `app` | Always `"SecureOfflineTranscriber"` |
| `format` | `2` (the file layout this program reads) |
| `list_version` | Date-style version, e.g. `"2026.11.03"`; a list older than the built-in one is refused |
| `min_app_version` | Oldest program version that may use this list, e.g. `"2.1.1"` |
| `recommended` | `ram_gb` (12), `below` and `at_or_above`: the model ids of the recommended set for PCs with less / more memory |
| `automatic` | For `en`, `fr`, `yue`, `zh`: model ids in the order Automatic tries them (the first installed one is used) |
| `models` | The list of models (below) |

## Model fields

| Field | Example | Rules |
|---|---|---|
| `id` | `"whisper-small"` | Lower-case letters, digits, `.` `-` `_`; it is the folder name in the models folder, so keep it when you update a model |
| `name` | `"Whisper small"` | Shown in Options and in file names' statistics |
| `kind` | `"whisper"` | One of `whisper` (CTranslate2/faster-whisper), `sensevoice`, `parakeet`, `moonshine` (sherpa-onnx) |
| `languages` | `["en", "fr", "yue", "zh"]` | Any of these four |
| `repo` | `"Systran/faster-whisper-small"` | A Hugging Face model repo |
| `revision` | 40-character commit id | Write `"main"` and let the tool fill it in |
| `files` | `["model.bin", "config.json", ...]` | Plain file names in the repo; the tool turns them into name + size + SHA-256 |
| `license`, `license_url` | `"MIT"`, `"https://…"` | Shown in the model manager and the download prompt |
| `offered` | `"Must"` / `"Should"` / `"Could"` | Sort order in the model manager |
| `note` | `"Fast English and French"` | Tooltip on the model's name |

Files each kind needs (the list is refused otherwise):
- **whisper**: `model.bin`, `config.json`, `tokenizer.json`, `vocabulary.txt` or `vocabulary.json`
- **sensevoice**: `model.int8.onnx` (or `model.onnx`), `tokens.txt`
- **parakeet**: `encoder…onnx`, `decoder…onnx`, `joiner…onnx`, `tokens.txt`
- **moonshine**: `preprocess.onnx`, `encode…onnx`, `uncached_decode…onnx`, `cached_decode…onnx`, `tokens.txt`

## Safety

A list file can only point at huggingface.co, at a fixed commit, and every downloaded file must match its
SHA-256 or it is deleted. File names cannot contain folders. So a list file cannot make the program download
or run anything else — but only import lists from people you trust, as with any file.
