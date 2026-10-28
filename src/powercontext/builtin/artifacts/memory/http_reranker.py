# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""HTTP reranking service adapter for the Memory family.

The challenge contract fixes the import path of this adapter, so the module
lives next to the Memory contracts it serves and re-exports the implementation
that owns the batching, validation and global selection.
"""

from powercontext.service.rerank.protocol import (
    HTTPRerankCandidate,
    HTTPRerankRequest,
    HTTPRerankResponse,
    HTTPRerankScore,
)
from powercontext.service.rerank.reranker import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_POLICY_ID,
    HTTPMemoryReranker,
)

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_POLICY_ID",
    "HTTPMemoryReranker",
    "HTTPRerankCandidate",
    "HTTPRerankRequest",
    "HTTPRerankResponse",
    "HTTPRerankScore",
]
