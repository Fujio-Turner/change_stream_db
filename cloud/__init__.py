# Cloud blob storage output modules
# Each module implements a cloud-specific output forwarder.


def create_cloud_output(out_cfg: dict, dry_run: bool = False, metrics=None):
    """Factory: create the correct cloud forwarder for the configured mode."""
    mode = out_cfg.get("mode", "")
    if mode == "s3":
        from .cloud_s3 import S3OutputForwarder

        return S3OutputForwarder(out_cfg, dry_run, metrics=metrics)
    raise ValueError(f"Unknown cloud output mode: {mode}")
