# nix/module.nix — NixOS module for kraken-python
#
# Provides the package on PATH and optional credential configuration.
# Import from the flake:
#
#   inputs.kraken-python.url = "github:Cairnstew/kraken-python";
#
#   imports = [ inputs.kraken-python.nixosModules.default ];
#   services.kraken-python.enable = true;

{ config, lib, pkgs, ... }:

let
  cfg = config.services.kraken-python;
in
{
  options.services.kraken-python = {
    enable = lib.mkEnableOption "kraken-python CLI";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.kraken-python;
      defaultText = "pkgs.kraken-python";
      description = "The kraken-python package to use.";
    };

    # Credential environment variables.  These are written to a
    # mode-0600 EnvironmentFile and loaded by any systemd service
    # that uses this module.  For interactive use, source the file
    # or set the variables in your shell environment directly.
    credentials = {
      apiKey = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "KRAKEN_API_KEY value (public API key).";
      };

      apiSecret = lib.mkOption {
        type = lib.types.str;
        default = "";
        description = "KRAKEN_API_SECRET value (private signing secret).";
      };
    };

    # Path to an existing .env file (alternative to setting individual
    # credential options above).  When set, this takes precedence over
    # the individual credential options.
    envFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Path to a .env file with KRAKEN_* variables.";
    };
  };

  config = lib.mkIf cfg.enable {
    # Make the package available system-wide.
    environment.systemPackages = [ cfg.package ];

    # Write a credentials file if individual options are provided.
    # The file is mode 0600 and owned by root, loadable by systemd.
    systemd.services.kraken-python-env = lib.mkIf (cfg.envFile == null && cfg.credentials.apiKey != "") {
      description = "Write kraken-python credentials";
      wantedBy = [ "multi-user.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = ''
        mkdir -p /run/kraken-python
        cat > /run/kraken-python/.env <<'EOF'
        KRAKEN_API_KEY=${cfg.credentials.apiKey}
        KRAKEN_API_SECRET=${cfg.credentials.apiSecret}
        EOF
        chmod 0600 /run/kraken-python/.env
      '';
    };
  };
}