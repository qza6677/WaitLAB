from waitlab.app_composition import ApplicationContext, open_application_context


def test_application_context_builds_service_graph(tmp_path):
    context = open_application_context(tmp_path / "waitlab.db")
    try:
        assert isinstance(context, ApplicationContext)
        assert context.service.storage is context.storage

        task = context.service.add_manual_task("组合根测试", "验证")
        assert task.title == "组合根测试"
        assert context.service.list_manual_tasks() == [task]
    finally:
        context.close()


def test_application_context_can_disable_legacy_ai_tracking(tmp_path):
    context = ApplicationContext.open(tmp_path / "waitlab.db", track_ai_time=False)
    try:
        assert context.storage.track_ai_time is False
    finally:
        context.close()
