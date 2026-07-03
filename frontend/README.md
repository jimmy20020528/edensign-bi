# Edensign BI Frontend

Simple standalone frontend for the BI API.

## Run

From `bi/frontend`:

```bash
python3 -m http.server 5173
```

Then open:

[`http://localhost:5173`](http://localhost:5173)

Make sure API is running (default expected):

`http://localhost:8000`

## Features

- Query by ZIP + objective + scoring mode
- Default scoring mode = `hybrid`
- Auto fallback to `heuristic` when warnings include `small_zip_low_support`
- Shows top-3 recommendations, warnings, and model artifact paths
