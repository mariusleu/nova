# Copyright (c) 2019 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import time

import mock

import nova.conf
from nova import objects
from nova.scheduler.filters import bigvm_filter
from nova import test
from nova.tests.unit.scheduler import fakes
from nova.tests import uuidsentinel

CONF = nova.conf.CONF


@mock.patch('nova.scheduler.client.report.'
            'SchedulerReportClient._get_inventory')
class TestBigVmBaseFilter(test.NoDBTestCase):

    def setUp(self):
        super(TestBigVmBaseFilter, self).setUp()
        self.filt_cls = bigvm_filter.BigVmBaseFilter()
        self.hv_size = CONF.bigvm_mb + 1024

    def test_big_vm_host_without_inventory(self, mock_inv):
        mock_inv.return_value = {}
        host = fakes.FakeHostState('host1', 'compute',
                {'free_ram_mb': self.hv_size,
                 'total_usable_ram_mb': self.hv_size,
                 'uuid': uuidsentinel.host1})
        self.assertIsNone(self.filt_cls._get_hv_size(host))

    def test_big_vm_host_with_placement_error(self, mock_inv):
        mock_inv.return_value = None
        host = fakes.FakeHostState('host1', 'compute',
                {'free_ram_mb': self.hv_size,
                 'total_usable_ram_mb': self.hv_size,
                 'uuid': uuidsentinel.host1})
        self.assertIsNone(self.filt_cls._get_hv_size(host))

    def test_big_vm_host_with_empty_inventory(self, mock_inv):
        mock_inv.return_value = {'inventories': {}}
        host = fakes.FakeHostState('host1', 'compute',
                {'free_ram_mb': self.hv_size,
                 'total_usable_ram_mb': self.hv_size,
                 'uuid': uuidsentinel.host1})
        self.assertIsNone(self.filt_cls._get_hv_size(host))

    def test_big_vm_get_hv_size_with_cache(self, mock_inv):
        mock_inv.return_value = {}
        host = fakes.FakeHostState('host1', 'compute',
                {'free_ram_mb': self.hv_size,
                 'total_usable_ram_mb': self.hv_size,
                 'uuid': uuidsentinel.host1})
        self.filt_cls._HV_SIZE_CACHE = {
            host.uuid: 1234,
            'last_modified': time.time()
        }
        self.assertEqual(self.filt_cls._get_hv_size(host), 1234)

    def test_big_vm_get_hv_size_cache_timeout(self, mock_inv):
        mock_inv.return_value = {'inventories': {'MEMORY_MB':
                                                    {'max_unit': 23}}}
        host = fakes.FakeHostState('host1', 'compute',
                {'free_ram_mb': self.hv_size,
                 'total_usable_ram_mb': self.hv_size,
                 'uuid': uuidsentinel.host1})
        mod = time.time() - self.filt_cls._HV_SIZE_CACHE_RETENTION_TIME
        self.filt_cls._HV_SIZE_CACHE = {
            host.uuid: 1234,
            'last_modified': mod
        }
        self.assertEqual(self.filt_cls._get_hv_size(host), 23)


class TestBigVmClusterUtilizationFilter(test.NoDBTestCase):

    def setUp(self):
        super(TestBigVmClusterUtilizationFilter, self).setUp()
        self.hv_size = CONF.bigvm_mb + 1024
        self.filt_cls = bigvm_filter.BigVmClusterUtilizationFilter()
        self.filt_cls._HV_SIZE_CACHE = {
            uuidsentinel.host1: self.hv_size,
            'last_modified': time.time()
        }

    def test_big_vm_with_small_vm_passes(self):
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=1024, extra_specs={}))
        host = fakes.FakeHostState('host1', 'compute', {})
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))

    def test_baremetal_instance_passes(self):
        extra_specs = {'capabilities:cpu_arch': 'x86_64'}
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb,
                                  extra_specs=extra_specs))
        host = fakes.FakeHostState('host1', 'compute', {})
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_without_hv_size(self):
        """If there's no inventory for this host, it should not even have
        passed placement API checks, so we stop it here.
        """
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb, extra_specs={}))
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1})
        self.filt_cls._HV_SIZE_CACHE[host.uuid] = None
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_without_enough_ram(self):
        # there's enough RAM available in the cluster but not enough (~50 % of
        # the requested size on average
        # 12 hosts (bigvm + 1 GB size)
        # 11 big VM + some smaller (12 * 1 GB) already deployed
        # -> still bigvm_mb left, but ram utilization ratio of all hosts is too
        # high
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb, extra_specs={}))
        total_ram = self.hv_size * 12
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1,
                 'free_ram_mb': CONF.bigvm_mb,
                 'total_usable_ram_mb': total_ram})
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_without_enough_ram_ignores_ram_ratio(self):
        # same as test_big_vm_without_enough_ram but with more theoretical RAM
        # via `ram_allocation_ratio`. big VMs reserve all memory so the ratio
        # does not count for them.
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb, extra_specs={}))
        total_ram = self.hv_size * 12
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1,
                 'free_ram_mb': CONF.bigvm_mb,
                 'total_usable_ram_mb': total_ram,
                 'ram_allocation_ratio': 1.5})
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_without_enough_ram_percent(self):
        # there's just closely not enough RAM available
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb, extra_specs={}))
        total_ram = self.hv_size * 12
        hv_percent = self.filt_cls._get_max_ram_percent(CONF.bigvm_mb,
                                                        self.hv_size)
        free_ram_mb = total_ram - (total_ram * hv_percent / 100.0) - 128
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1,
                 'free_ram_mb': free_ram_mb,
                 'total_usable_ram_mb': total_ram})
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_with_enough_ram(self):
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb, extra_specs={}))
        total_ram = self.hv_size * 12
        hv_percent = self.filt_cls._get_max_ram_percent(CONF.bigvm_mb,
                                                        self.hv_size)
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1,
                 'free_ram_mb': total_ram - (total_ram * hv_percent / 100.0),
                 'total_usable_ram_mb': total_ram})
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))


class TestBigVmFlavorHostSizeFilter(test.NoDBTestCase):
    def setUp(self):
        super(TestBigVmFlavorHostSizeFilter, self).setUp()
        self.hv_size = CONF.bigvm_mb + 1024
        self.filt_cls = bigvm_filter.BigVmFlavorHostSizeFilter()
        self.filt_cls._HV_SIZE_CACHE = {
            uuidsentinel.host1: self.hv_size,
            'last_modified': time.time()
        }

    def test_big_vm_with_small_vm_passes(self):
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=1024, extra_specs={}))
        host = fakes.FakeHostState('host1', 'compute', {})
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))

    def test_baremetal_instance_passes(self):
        extra_specs = {'capabilities:cpu_arch': 'x86_64'}
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb,
                                  extra_specs=extra_specs))
        host = fakes.FakeHostState('host1', 'compute', {})
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_without_hv_size(self):
        """If there's no inventory for this host, it should not even have
        passed placement API checks, so we stop it here.
        """
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb, extra_specs={}))
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1})
        self.filt_cls._HV_SIZE_CACHE[host.uuid] = None
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_memory_match_with_tolerance(self):
        """We only accept tolerance below not above the given value."""

        def call(a, b):
            return self.filt_cls._memory_match_with_tolerance(a, b)

        self.filt_cls._HV_SIZE_TOLERANCE_PERCENT = 10
        self.assertTrue(call(1024, 1024 - 1024 * 0.1))
        self.assertFalse(call(1024, 1024 - 1024 * 0.1 - 1))
        self.assertTrue(call(1024, 1024))
        self.assertFalse(call(1024, 1025))

        self.filt_cls._HV_SIZE_TOLERANCE_PERCENT = 50
        self.assertTrue(call(1024, 512))
        self.assertFalse(call(1024, 511))

    def test_big_vm_with_matching_full_size(self):
        """Test automatic full size matching."""
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb, extra_specs={}))
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1})
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_with_matching_half_size(self):
        """Test automatic full size matching."""
        CONF.set_override('bigvm_host_size_filter_host_fractions',
                          {'full': 1, 'half': 0.5}, 'filter_scheduler')
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb, extra_specs={}))
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1})
        self.filt_cls._HV_SIZE_CACHE[host.uuid] = CONF.bigvm_mb * 2 + 1024
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_with_half_size_not_defined(self):
        """Test automatic full size matching."""
        CONF.set_override('bigvm_host_size_filter_host_fractions',
                          {'full': 1}, 'filter_scheduler')
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb, extra_specs={}))
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1})
        self.filt_cls._HV_SIZE_CACHE[host.uuid] = CONF.bigvm_mb * 2 + 1024
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_big_vm_without_matching_size(self):
        """Fails both half and full size test"""
        CONF.set_override('bigvm_host_size_filter_host_fractions',
                          {'full': 1, 'half': 0.5}, 'filter_scheduler')
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb, extra_specs={}))
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1})
        self.filt_cls._HV_SIZE_CACHE[host.uuid] = CONF.bigvm_mb * 1.5 + 1024
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_extra_specs_without_key(self):
        """If we don't have the extra spec set, we fail"""
        CONF.set_override('bigvm_host_size_filter_uses_flavor_extra_specs',
                          True, 'filter_scheduler')
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb, extra_specs={},
                                  name='random-name'))
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1})
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_extra_specs_invalid_value(self):
        """invalid value in extra specs makes it unscheduleable"""
        CONF.set_override('bigvm_host_size_filter_uses_flavor_extra_specs',
                          True, 'filter_scheduler')
        CONF.set_override('bigvm_host_size_filter_host_fractions',
                          {'full': 1, 'half': 0.5}, 'filter_scheduler')
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb,
                                  extra_specs={'host_fraction': 'any'},
                                  name='random-name'))
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1})
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_extra_specs_full_positive(self):
        """test specified full size"""
        CONF.set_override('bigvm_host_size_filter_uses_flavor_extra_specs',
                          True, 'filter_scheduler')
        CONF.set_override('bigvm_host_size_filter_host_fractions',
                          {'full': 1, 'half': 0.5}, 'filter_scheduler')
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb,
                                  extra_specs={'host_fraction': 'full'},
                                  name='random-name'))
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1})
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))

    def test_extra_specs_full_negative(self):
        """test specified full size"""
        CONF.set_override('bigvm_host_size_filter_uses_flavor_extra_specs',
                          True, 'filter_scheduler')
        CONF.set_override('bigvm_host_size_filter_host_fractions',
                          {'full': 1, 'half': 0.5}, 'filter_scheduler')
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb,
                                  extra_specs={'host_fraction': 'full'},
                                  name='random-name'))
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1})
        self.filt_cls._HV_SIZE_CACHE[host.uuid] = CONF.bigvm_mb * 2 + 1024
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))

    def test_extra_specs_half_positive(self):
        """test specified half size"""
        CONF.set_override('bigvm_host_size_filter_uses_flavor_extra_specs',
                          True, 'filter_scheduler')
        CONF.set_override('bigvm_host_size_filter_host_fractions',
                          {'full': 1, 'half': 0.5}, 'filter_scheduler')
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb,
                                  extra_specs={'host_fraction': 'full,half'},
                                  name='random-name'))
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1})
        self.filt_cls._HV_SIZE_CACHE[host.uuid] = CONF.bigvm_mb * 2 + 1024
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))

    def test_extra_specs_half_positive_with_unknown(self):
        """test specified half size"""
        CONF.set_override('bigvm_host_size_filter_uses_flavor_extra_specs',
                          True, 'filter_scheduler')
        CONF.set_override('bigvm_host_size_filter_host_fractions',
                          {'full': 1, 'half': 0.5}, 'filter_scheduler')
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb,
                                  extra_specs={'host_fraction': 'broken,half'},
                                  name='random-name'))
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1})
        self.filt_cls._HV_SIZE_CACHE[host.uuid] = CONF.bigvm_mb * 2 + 1024
        self.assertTrue(self.filt_cls.host_passes(host, spec_obj))

    def test_extra_specs_half_negative(self):
        """test specified half size"""
        CONF.set_override('bigvm_host_size_filter_uses_flavor_extra_specs',
                          True, 'filter_scheduler')
        CONF.set_override('bigvm_host_size_filter_host_fractions',
                          {'full': 1, 'half': 0.5}, 'filter_scheduler')
        spec_obj = objects.RequestSpec(
            flavor=objects.Flavor(memory_mb=CONF.bigvm_mb,
                                  extra_specs={'host_fraction': 'half'},
                                  name='random-name'))
        host = fakes.FakeHostState('host1', 'compute',
                {'uuid': uuidsentinel.host1})
        self.assertFalse(self.filt_cls.host_passes(host, spec_obj))
