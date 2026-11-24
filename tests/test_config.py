from edge.config import load_config


def test_defaults_three_nodes_and_demo_values(tmp_path):
    cfg = load_config(tmp_path / "missing.env", db_path=tmp_path / "b.db")
    assert [n.node_id for n in cfg.nodes] == ["node-a", "node-b", "node-c"]
    assert cfg.tick_seconds == 5.0
    assert cfg.batch_size == 50
    assert cfg.buffer_max_rows == 6048
    assert cfg.flow_zero_sustained_s == 30.0
    assert cfg.node_inactive_s == 90.0
    assert cfg.derivative_window_s == 50.0
    assert cfg.validity_fault_samples == 3
    assert cfg.use_mqtt is False

def test_env_tokens_are_read(tmp_path):
    (tmp_path / ".env").write_text("TB_HOST=demo.thingsboard.io\nTB_TOKEN_NODE_A=abc\n")
    cfg = load_config(tmp_path / ".env")
    assert cfg.tb_host == "demo.thingsboard.io"
    assert cfg.tokens["node-a"] == "abc"
