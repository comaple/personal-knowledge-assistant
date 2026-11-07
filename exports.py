"""Complete an export before sending HTTP success; no secrets or chat in the ZIP."""

import json
import tempfile
import zipfile

from store import now


def export_zip(store):
    state, documents = store.export_snapshot()
    result = tempfile.TemporaryFile()
    try:
        entries = []
        with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED) as archive:
            for row in documents:
                file = "documents/" + str(row["id"]) + ".md"
                archive.writestr(file, row["content"].encode("utf-8"))
                entries.append(
                    {
                        **{
                            key: row[key]
                            for key in (
                                "id",
                                "name",
                                "kind",
                                "tags",
                                "digest",
                                "version",
                                "created_at",
                                "updated_at",
                            )
                        },
                        "file": file,
                    }
                )
            archive.writestr(
                "metadata.json",
                json.dumps(
                    {
                        "schema_version": 1,
                        "exported_at": now(),
                        "revision": state["revision"],
                        "catalog_revision": state["catalog_revision"],
                        "documents": entries,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
        result.seek(0)
        return result
    except BaseException:
        result.close()
        raise
