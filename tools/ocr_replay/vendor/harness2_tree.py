# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Extracted path normalizer from Harness² ``harness2/core/tree.py``.

Source commit: 1cfe254e8d25b15f37048c3730134686abe60335.
Only ``normalize_rel`` was copied; its body is unchanged. H01 uses it before
resolving manifest paths so ``..``/absolute escapes are rejected consistently.
The Apache-2.0 license is in ``LICENSE`` and the modification record is in
``NOTICE``.
"""


def normalize_rel(rel: str) -> str:
    """Normalise a relative path, rejecting escapes before it is joined to a directory."""
    r = str(rel).strip().replace("\\", "/")
    if r.startswith("/") or ".." in r.split("/"):
        raise ValueError(f"path escapes the harness: {rel!r}")
    while r.startswith("./"):
        r = r[2:]
    return r.strip("/")
