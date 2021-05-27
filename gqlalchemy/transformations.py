# Copyright (c) 2016-2021 Memgraph Ltd. [https://memgraph.com]
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import multiprocessing as mp
from typing import Any, Dict, Iterator, List, Union

import mgclient
import networkx as nx

from gqlalchemy.utilities import to_cypher_labels, to_cypher_properties

__all__ = ("nx_to_cypher", "nx_graph_to_memgraph_parallel")


class NetworkXGraphConstants:
    LABELS = "labels"
    TYPE = "type"
    ID = "id"


def nx_to_cypher(graph: nx.Graph) -> Iterator[str]:
    """Generates a Cypher queries for creating graph"""

    yield from _nx_nodes_to_cypher(graph)
    yield from _nx_edges_to_cypher(graph)


def nx_graph_to_memgraph_parallel(graph: nx.Graph, host: str = "127.0.0.1", port: int = 7687) -> None:
    """Generates a Cypher queries and inserts data into Memgraph in parallel"""
    num_of_processes = mp.cpu_count() // 2
    for queries_gen in [_nx_nodes_to_cypher(graph), _nx_edges_to_cypher(graph)]:
        queries = list(queries_gen)
        chunk_size = len(queries) // num_of_processes
        processes = []
        for i in range(num_of_processes):
            process_queries = queries[i * chunk_size : chunk_size * (i + 1)]
            processes.append(
                mp.Process(
                    target=_insert_queries,
                    args=(process_queries, host, port),
                )
            )
        for p in processes:
            p.start()
        for p in processes:
            p.join()


def _insert_queries(queries: List[str], host: str, port: int) -> None:
    """Used by multiprocess insertion of nx into memgraph, works on a chunk of queries."""
    conn = mgclient.connect(host=host, port=port)
    while len(queries) > 0:
        try:
            query = queries.pop()
            cursor = conn.cursor()
            cursor.execute(query)
            cursor.fetchall()
        except IndexError:
            break
        except mgclient.DatabaseError as e:
            queries.append(query)
            logging.getLogger(__file__).warning(f"Ignoring database error: {str(e)}")
            continue
        conn.commit()


def _nx_nodes_to_cypher(graph: nx.Graph) -> Iterator[str]:
    """Generates a Cypher queries for creating nodes"""
    for nx_id, data in graph.nodes(data=True):
        yield _create_node(nx_id, data)


def _nx_edges_to_cypher(graph: nx.Graph) -> Iterator[str]:
    """Generates a Cypher queries for creating edges"""
    for n1, n2, data in graph.edges(data=True):
        from_label = graph.nodes[n1].get(NetworkXGraphConstants.LABELS, "")
        to_label = graph.nodes[n2].get(NetworkXGraphConstants.LABELS, "")
        yield _create_edge(n1, n2, from_label, to_label, data)


def _create_node(nx_id: int, properties: Dict[str, Any]) -> str:
    """Returns Cypher query for node creation"""
    if "id" not in properties:
        properties["id"] = nx_id
    labels_str = to_cypher_labels(properties.get(NetworkXGraphConstants.LABELS, ""))
    properties_without_labels = {k: v for k, v in properties.items() if k != NetworkXGraphConstants.LABELS}
    properties_str = to_cypher_properties(properties_without_labels)

    return f"CREATE ({labels_str} {properties_str});"


def _create_edge(
    from_id: int,
    to_id: int,
    from_label: Union[str, List[str]],
    to_label: Union[str, List[str]],
    properties: Dict[str, Any],
) -> str:
    """Returns Cypher query for edge creation."""
    edge_type = to_cypher_labels(properties.get(NetworkXGraphConstants.TYPE, "TO"))
    properties.pop(NetworkXGraphConstants.TYPE, None)
    properties_str = to_cypher_properties(properties)
    from_label_str = to_cypher_labels(from_label)
    to_label_str = to_cypher_labels(to_label)

    return f"MATCH (n{from_label_str} {{id: {from_id}}}), (m{to_label_str} {{id: {to_id}}}) CREATE (n)-[{edge_type} {properties_str}]->(m);"
