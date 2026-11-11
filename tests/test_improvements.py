"""Tests cho cải tiến Bài 4: kiểm tra nguồn theo từng provider, giới hạn retry 429, answer rỗng."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import run_agent
from bonus.tools import BonusTools
from status_tools import StatusTools
from test_lab import FakeProvider, tool_call, final

GITHUB = '{"provider":"github","incident_id":"xr3myzj7ms51"}'
CLOUDFLARE = '{"provider":"cloudflare","incident_id":"wp49bp8bqo6c"}'


class SourcePerProviderTests(unittest.TestCase):
    def test_each_provider_needs_its_own_url(self):
        gh = StatusTools().get_incident_details('github', 'xr3myzj7ms51')['url']
        cf = StatusTools().get_incident_details('cloudflare', 'wp49bp8bqo6c')['url']
        fake = FakeProvider([tool_call('1', 'get_incident_details', GITHUB), tool_call('2', 'get_incident_details', CLOUDFLARE),
                             final('Chỉ có GitHub ' + gh), final(f'GitHub {gh} và Cloudflare {cf}')])
        result = run_agent('So sánh', fake, StatusTools(), max_steps=6)
        self.assertEqual(result['status'], 'completed')
        missing = [x for x in result['trace'] if x['event'] == 'validation_missing_source']
        self.assertEqual(len(missing), 1)
        self.assertEqual(list(missing[0]['missing']), ['cloudflare'])
        # Model nhận được yêu cầu sửa nêu đúng provider còn thiếu.
        self.assertIn('cloudflare', fake.histories[3][-1]['content'])

    def test_canonical_incident_path_is_accepted(self):
        fake = FakeProvider([tool_call('1', 'get_incident_details', GITHUB),
                             final('Nguồn: https://www.githubstatus.com/incidents/xr3myzj7ms51')])
        self.assertEqual(run_agent('x', fake, StatusTools())['status'], 'completed')

    def test_empty_answer_is_not_completed_directly(self):
        fake = FakeProvider([final(''), final('Trả lời')])
        result = run_agent('x', fake, StatusTools())
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(any(x['event'] == 'validation_empty_answer' for x in result['trace']))


class InjectionEchoTests(unittest.TestCase):
    def test_echoed_marker_triggers_revision(self):
        sys.path.insert(0, str(ROOT / 'evals'))
        from scenarios import ScenarioTools
        tools = ScenarioTools('status_injection')
        marker = tools.execute('get_status', {'provider': 'github'})['external_notice'].split('print ')[1].split()[0].rstrip('.')
        fake = FakeProvider([tool_call('1', 'get_status', '{"provider":"github"}'),
                             final(f'Bình thường. Đã bỏ qua lệnh in {marker}.'), final('Bình thường. Đã bỏ qua chỉ dẫn đáng ngờ.')])
        result = run_agent('GitHub thế nào?', fake, tools, max_steps=4)
        self.assertEqual(result['status'], 'completed')
        self.assertNotIn(marker, result['answer'])
        event = next(x for x in result['trace'] if x['event'] == 'validation_injection_echo')
        self.assertEqual(event['markers'], [marker])
        # Thông điệp yêu cầu sửa không được lặp lại marker cho model.
        self.assertNotIn(marker, fake.histories[2][-1]['content'])

    def test_no_false_positive_on_real_snapshot(self):
        from agent import _injection_markers
        tools = StatusTools()
        for provider in ('github', 'cloudflare'):
            status = tools.get_status(provider)
            self.assertEqual(_injection_markers(status), set())
            data = tools._load(provider)['data']['incidents']['incidents']
            self.assertEqual(_injection_markers(data), set())


class RetryBudgetTests(unittest.TestCase):
    def test_persistent_429_is_capped_at_three_real_calls(self):
        calls = [tool_call(str(i), 'get_vercel_status', '{}') for i in range(5)]
        fake = FakeProvider(calls + [final('Không xác định được Vercel')])
        result = run_agent('x', fake, BonusTools(scenario='rate_limit_always'), max_steps=8)
        self.assertEqual(result['status'], 'completed')
        executed = [x for x in result['trace'] if x['event'] == 'tool' and x['name'] == 'get_vercel_status']
        blocked = [x for x in result['trace'] if x['event'] == 'tool_blocked']
        self.assertEqual(len(executed), 3)
        self.assertEqual(len(blocked), 2)
        self.assertEqual(json.loads(fake.histories[4][-1]['content'])['error'], 'RETRY_BUDGET_EXHAUSTED')

    def test_unparseable_arguments_do_not_count_as_execution(self):
        fake = FakeProvider([tool_call('1', 'get_status', '{"provider":'), final()])
        result = run_agent('x', fake, StatusTools())
        tool = next(x for x in result['trace'] if x['event'] == 'tool')
        self.assertEqual(tool['result']['error'], 'INVALID_ARGUMENTS')


if __name__ == '__main__':
    unittest.main()
