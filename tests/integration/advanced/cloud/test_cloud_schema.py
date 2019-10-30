# Copyright DataStax, Inc.
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
# limitations under the License
"""
This is mostly copypasta from integration/long/test_schema.py

TODO: Come up with way to run cloud and local tests without duplication
"""

import logging
import time

from cassandra import ConsistencyLevel
from cassandra.cluster import Cluster
from cassandra.query import SimpleStatement

from tests.integration import execute_until_pass
from tests.integration.advanced.cloud import CloudProxyCluster

log = logging.getLogger(__name__)


class CloudSchemaTests(CloudProxyCluster):
    def test_recreates(self):
        """
        Basic test for repeated schema creation and use, using many different keyspaces
        """
        self.connect(self.creds)
        session = self.session

        for _ in self.cluster.metadata.all_hosts():
            for keyspace_number in range(5):
                keyspace = "ks_{0}".format(keyspace_number)

                if keyspace in self.cluster.metadata.keyspaces.keys():
                    drop = "DROP KEYSPACE {0}".format(keyspace)
                    log.debug(drop)
                    execute_until_pass(session, drop)

                create = "CREATE KEYSPACE {0} WITH replication = {{'class': 'SimpleStrategy', 'replication_factor': 3}}".format(
                    keyspace)
                log.debug(create)
                execute_until_pass(session, create)

                create = "CREATE TABLE {0}.cf (k int PRIMARY KEY, i int)".format(keyspace)
                log.debug(create)
                execute_until_pass(session, create)

                use = "USE {0}".format(keyspace)
                log.debug(use)
                execute_until_pass(session, use)

                insert = "INSERT INTO cf (k, i) VALUES (0, 0)"
                log.debug(insert)
                ss = SimpleStatement(insert, consistency_level=ConsistencyLevel.QUORUM)
                execute_until_pass(session, ss)

    def test_for_schema_disagreement_attribute(self):
        """
        Tests to ensure that schema disagreement is properly surfaced on the response future.

        Creates and destroys keyspaces/tables with various schema agreement timeouts set.
        First part runs cql create/drop cmds with schema agreement set in such away were it will be impossible for agreement to occur during timeout.
        It then validates that the correct value is set on the result.
        Second part ensures that when schema agreement occurs, that the result set reflects that appropriately

        @since 3.1.0
        @jira_ticket PYTHON-458
        @expected_result is_schema_agreed is set appropriately on response thefuture

        @test_category schema
        """
        # This should yield a schema disagreement
        cloud_config = {'secure_connect_bundle': self.creds}
        cluster = Cluster(max_schema_agreement_wait=0.001, protocol_version=4, cloud=cloud_config)
        session = cluster.connect(wait_for_all_pools=True)

        rs = session.execute(
            "CREATE KEYSPACE test_schema_disagreement WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 3}")
        self.check_and_wait_for_agreement(session, rs, False)
        rs = session.execute(
            SimpleStatement("CREATE TABLE test_schema_disagreement.cf (key int PRIMARY KEY, value int)",
                            consistency_level=ConsistencyLevel.ALL))
        self.check_and_wait_for_agreement(session, rs, False)
        rs = session.execute("DROP KEYSPACE test_schema_disagreement")
        self.check_and_wait_for_agreement(session, rs, False)
        cluster.shutdown()

        # These should have schema agreement
        cluster = Cluster(protocol_version=4, max_schema_agreement_wait=100, cloud=cloud_config)
        session = cluster.connect()
        rs = session.execute(
            "CREATE KEYSPACE test_schema_disagreement WITH replication = {'class': 'SimpleStrategy', 'replication_factor': 3}")
        self.check_and_wait_for_agreement(session, rs, True)
        rs = session.execute(
            SimpleStatement("CREATE TABLE test_schema_disagreement.cf (key int PRIMARY KEY, value int)",
                            consistency_level=ConsistencyLevel.ALL))
        self.check_and_wait_for_agreement(session, rs, True)
        rs = session.execute("DROP KEYSPACE test_schema_disagreement")
        self.check_and_wait_for_agreement(session, rs, True)
        cluster.shutdown()

    def check_and_wait_for_agreement(self, session, rs, exepected):
        # Wait for RESULT_KIND_SCHEMA_CHANGE message to arrive
        time.sleep(1)
        self.assertEqual(rs.response_future.is_schema_agreed, exepected)
        if not rs.response_future.is_schema_agreed:
            session.cluster.control_connection.wait_for_schema_agreement(wait_time=1000)