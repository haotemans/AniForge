"""Exercise window modal dispatch with timer events; no GUI and no rendering."""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import bpy
import mmd_lora_studio as addon

assert 'timer' not in bpy.types.Event.bl_rna.properties


class Driver:
    modal = addon.ML_OT_render.modal
    finish = addon.ML_OT_render.finish

    def __init__(self, count=3, fail_render=False, fail_close=False):
        self._timer = object()
        self._finished = self._in_step = False
        self._next_step = 0.0
        self.index = 0
        self.shots = addon.recipe()[:count]
        self.rendered = []; self.closed = []; self.removed = []; self.reports = []
        self.ends = 0
        self._session = SimpleNamespace(folder=Path('unused'), render=self.render, close=self.close)
        self.settings = SimpleNamespace(running=True, progress='', api_caption=False)
        self.fail_render = fail_render; self.fail_close = fail_close
        manager = SimpleNamespace(event_timer_remove=self.removed.append, progress_end=self.end,
                                  progress_update=lambda index: None)
        self.context = SimpleNamespace(scene=SimpleNamespace(ml_settings=self.settings), window_manager=manager, area=None)

    def render(self, context, shot):
        if self.fail_render:
            raise RuntimeError('Injected render failure')
        # A notification during the blocking render must not run another shot.
        assert self.modal(context, SimpleNamespace(type='TIMER')) == {'RUNNING_MODAL'}
        self.rendered.append(shot)

    def close(self, status, error):
        self.closed.append((status, error))
        if self.fail_close:
            raise RuntimeError('Injected cleanup failure')

    def end(self):
        self.ends += 1

    def report(self, level, text):
        self.reports.append((level, text))


clock = [100.0]
timer_event = SimpleNamespace(type='TIMER')  # Deliberately has no .timer.
with patch.object(addon, 'monotonic', lambda: clock[0]):
    for count in (3, 16):
        driver = Driver(count)
        assert driver.modal(driver.context, SimpleNamespace(type='MOUSEMOVE')) == {'RUNNING_MODAL'}
        for i in range(count):
            result = driver.modal(driver.context, timer_event)
            assert len(driver.rendered) == i+1
            if i+1 < count:
                for _ in range(20):
                    assert driver.modal(driver.context, timer_event) == {'RUNNING_MODAL'}
                assert len(driver.rendered) == i+1
            clock[0] += 1
        assert result == {'FINISHED'}
        assert driver.closed == [('complete', '')]
        assert not driver.settings.running and driver._timer is None and driver._session is None
        assert driver.ends == len(driver.removed) == 1
        driver.finish(driver.context, 'cancelled')
        assert driver.ends == len(driver.removed) == 1

    driver = Driver()
    assert driver.modal(driver.context, SimpleNamespace(type='ESC')) == {'CANCELLED'}
    assert not driver.rendered and driver.closed == [('cancelled', '')]
    assert not driver.settings.running and driver.ends == 1

    for fail_render, fail_close in ((True, False), (True, True), (False, True)):
        driver = Driver(1, fail_render=fail_render, fail_close=fail_close)
        assert driver.modal(driver.context, timer_event) == {'CANCELLED'}
        assert not driver.settings.running and driver.settings.progress.startswith('failed:')
        assert driver._timer is None and driver._session is None
        assert driver.ends == len(driver.removed) == 1
        assert not driver._in_step

    driver = Driver(1); driver.settings.api_caption = True
    def start_after_restore(context, folder):
        assert not driver.settings.running and driver.closed == [('complete', '')]
    with patch.object(addon.captioning, 'start', side_effect=start_after_restore) as start:
        assert driver.modal(driver.context, timer_event) == {'FINISHED'}
        start.assert_called_once_with(driver.context, Path('unused'))

print('MODAL_OK: real Event RNA, no timer attribute, cadence/reentry/3+16 dispatch, Esc and failure cleanup; zero renders')
