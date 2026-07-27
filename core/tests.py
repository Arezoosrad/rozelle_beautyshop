from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from rozelle_beautyshop.core.hooks import action_hook, list_hook_callbacks, register_hook, run_hook, unregister_hook
from rozelle_beautyshop.core.models import CronJob
from rozelle_beautyshop.core.utils import parse_cron_expression, should_run, update_next_run


class HookTests(TestCase):
    def test_register_and_run_action(self):
        result = []

        def test_func():
            result.append('called')

        register_hook('test_action', test_func)
        run_hook('test_action')
        self.assertIn('called', result)
        unregister_hook('test_action', test_func)

    def test_priority_order(self):
        order = []

        @action_hook('prio_test', priority=20)
        def low_prio():
            order.append('low')

        @action_hook('prio_test', priority=5)
        def high_prio():
            order.append('high')

        run_hook('prio_test')
        self.assertEqual(order, ['high', 'low'])

    def test_list_hook_callbacks(self):
        def callback():
            return None

        register_hook('list_test', callback)
        callbacks = list_hook_callbacks('list_test')
        self.assertTrue(any(item[0] == callback for item in callbacks))
        unregister_hook('list_test', callback)


class CronScheduleTests(TestCase):
    def test_interval_expression_is_not_treated_as_daily(self):
        self.assertEqual(parse_cron_expression('*/15 * * * *'), timedelta(minutes=15))

    def test_next_run_keeps_the_time_component(self):
        job = CronJob.objects.create(
            name='test interval', func_path='core.tests.noop',
            cron_expression='*/15 * * * *',
        )
        update_next_run(job)
        job.refresh_from_db()
        self.assertIsNotNone(job.next_run_time)
        self.assertFalse(should_run(job))
        job.next_run = timezone.localdate() - timedelta(days=1)
        job.save(update_fields=['next_run'])
        self.assertTrue(should_run(job))
