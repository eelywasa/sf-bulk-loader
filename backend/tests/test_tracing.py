"""Tests for optional OpenTelemetry tracing (SFBL-56)."""

import pytest
from unittest.mock import patch, MagicMock


class TestConfigureTracingDisabled:
    def test_noop_provider_installed_when_disabled(self):
        from opentelemetry.trace import NoOpTracerProvider
        from app.observability import tracing

        # Reset to allow re-configuration in tests
        tracing._configured = False

        class _FakeSettings:
            tracing_enabled = False

        tracing.configure_tracing(_FakeSettings())

        from opentelemetry import trace
        assert isinstance(trace.get_tracer_provider(), NoOpTracerProvider)
        tracing._configured = False

    def test_idempotent_when_called_twice(self):
        """configure_tracing must be safe to call multiple times."""
        from app.observability import tracing

        tracing._configured = False

        class _FakeSettings:
            tracing_enabled = False

        tracing.configure_tracing(_FakeSettings())
        call_count_before = tracing._configured
        tracing.configure_tracing(_FakeSettings())  # second call should short-circuit
        # _configured stays True, no exception
        assert tracing._configured is True
        tracing._configured = False


class TestTracingSpansNoOp:
    """Verify span context managers work correctly with a no-op provider."""

    def setup_method(self):
        from opentelemetry.trace import NoOpTracerProvider
        from opentelemetry import trace
        from app.observability import tracing

        tracing._configured = False
        trace.set_tracer_provider(NoOpTracerProvider())
        tracing._configured = True

    def teardown_method(self):
        from app.observability import tracing
        tracing._configured = False

    def test_run_span_does_not_raise(self):
        from app.observability.tracing import run_span

        with run_span("run-1", "plan-1") as span:
            assert span is not None

    def test_step_span_does_not_raise(self):
        from app.observability.tracing import step_span

        with step_span("step-1", "Account", "upsert") as span:
            assert span is not None

    def test_partition_span_does_not_raise(self):
        from app.observability.tracing import partition_span

        with partition_span("job-1") as span:
            assert span is not None

    def test_run_span_propagates_exception(self):
        from app.observability.tracing import run_span

        with pytest.raises(ValueError, match="boom"):
            with run_span("run-1", "plan-1"):
                raise ValueError("boom")

    def test_step_span_propagates_exception(self):
        from app.observability.tracing import step_span

        with pytest.raises(RuntimeError, match="step failed"):
            with step_span("step-1", "Contact", "insert"):
                raise RuntimeError("step failed")

    def test_partition_span_propagates_exception(self):
        from app.observability.tracing import partition_span

        with pytest.raises(RuntimeError, match="partition failed"):
            with partition_span("job-1"):
                raise RuntimeError("partition failed")


class TestTracingSpansWithInMemoryExporter:
    """Verify span attributes by injecting a real tracer via _get_tracer patch."""

    def setup_method(self):
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self._tracer = self.provider.get_tracer("test")

    def _get_tracer_patch(self):
        return self._tracer

    def test_run_span_sets_run_id(self):
        from app.observability import tracing

        with patch.object(tracing, "_get_tracer", self._get_tracer_patch):
            with tracing.run_span("run-abc", "plan-xyz"):
                pass

        spans = self.exporter.get_finished_spans()
        assert len(spans) == 1
        assert spans[0].attributes["run.id"] == "run-abc"

    def test_run_span_sets_load_plan_id(self):
        from app.observability import tracing

        with patch.object(tracing, "_get_tracer", self._get_tracer_patch):
            with tracing.run_span("run-abc", "plan-xyz"):
                pass

        spans = self.exporter.get_finished_spans()
        assert spans[0].attributes["load_plan.id"] == "plan-xyz"

    def test_run_span_name_is_run_execute(self):
        from app.observability import tracing

        with patch.object(tracing, "_get_tracer", self._get_tracer_patch):
            with tracing.run_span("run-abc", "plan-xyz"):
                pass

        spans = self.exporter.get_finished_spans()
        assert spans[0].name == "run.execute"

    def test_step_span_sets_step_attributes(self):
        from app.observability import tracing

        with patch.object(tracing, "_get_tracer", self._get_tracer_patch):
            with tracing.step_span("step-1", "Account", "upsert"):
                pass

        spans = self.exporter.get_finished_spans()
        assert spans[0].attributes["step.id"] == "step-1"
        assert spans[0].attributes["object.name"] == "Account"
        assert spans[0].attributes["operation"] == "upsert"

    def test_partition_span_sets_job_record_id(self):
        from app.observability import tracing

        with patch.object(tracing, "_get_tracer", self._get_tracer_patch):
            with tracing.partition_span("jr-999") as span:
                span.set_attribute("salesforce.job.id", "sf-job-abc")

        spans = self.exporter.get_finished_spans()
        assert spans[0].attributes["job_record.id"] == "jr-999"
        assert spans[0].attributes["salesforce.job.id"] == "sf-job-abc"

    def test_run_span_records_exception(self):
        from opentelemetry.trace import StatusCode
        from app.observability import tracing

        with patch.object(tracing, "_get_tracer", self._get_tracer_patch):
            with pytest.raises(ValueError):
                with tracing.run_span("run-abc", "plan-xyz"):
                    raise ValueError("something went wrong")

        spans = self.exporter.get_finished_spans()
        assert spans[0].status.status_code == StatusCode.ERROR

    def test_outcome_code_can_be_set_on_span(self):
        from app.observability import tracing

        with patch.object(tracing, "_get_tracer", self._get_tracer_patch):
            with tracing.run_span("run-abc", "plan-xyz") as span:
                span.set_attribute("outcome.code", "ok")

        spans = self.exporter.get_finished_spans()
        assert spans[0].attributes["outcome.code"] == "ok"
