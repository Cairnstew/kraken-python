# nix/module.nix — NixOS module for kraken-python
#
# Provides the package on PATH plus credential/environment configuration.
# Import from the flake:
#
#   inputs.kraken-python.url = "github:Cairnstew/kraken-python";
#
#   imports = [ inputs.kraken-python.nixosModules.default ];
#   services.kraken-python.enable = true;
#
# Credentials can be supplied three ways (first wins):
#   1. credentials.envFile     — path to an existing .env file (takes
#      precedence; no env file is generated at all).
#   2. credentials.api*File    — keyfile paths (e.g. agenix-managed
#      /run/secrets/...).  The secret is resolved by systemd at
#      activation time, so it never enters /nix/store.  The generated
#      env file defaults to /run/kraken-python/.env (mode 0600).
#   3. credentials.api*/...    — plain string values (kept for
#      convenience, but note they end up world-readable in the Nix
#      store; prefer the *File options for real secrets).
#
# The generated env file is loaded automatically by the app (python-dotenv)
# and is also suitable as a systemd EnvironmentFile:
#
#   systemd.services.foo.serviceConfig.EnvironmentFile =
#     [ config.services.kraken-python.envFilePath ];

{ config, lib, pkgs, ... }:

let
  cfg = config.services.kraken-python;

  # Single-quote a string for embedding in a sh script.
  shellQuote = v: "'" + builtins.replaceStrings [ "'" ] [ "'\\''" ] v + "'";

  # One KRAKEN_* line as literal shell that writes the value into the env
  # file.  Nix-supplied values are written literally (copying them into the
  # store is unavoidable); keyfile values are resolved by `cat` at runtime
  # so the secret never appears in the store.
  valueLine = name: value: "echo ${shellQuote "${name}='${value}'"}";
  fileLine = name: file: "printf '%s\\n' \"${name}=$(cat ${shellQuote file})\"";
in
{
  # ── Options ─────────────────────────────────────────────────────────────
  options.services.kraken-python = {
    enable = lib.mkEnableOption "kraken-python CLI";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.kraken-python;
      defaultText = "pkgs.kraken-python";
      description = "The kraken-python package to use.";
    };

    # Where the generated credential file is written.  Exposed so other
    # systemd services can reference it as an EnvironmentFile.
    envFilePath = lib.mkOption {
      type = lib.types.str;
      default = "/run/kraken-python/.env";
      description = "Path of the env file written by the credential oneshot.";
    };

    credentials = {
      # Plain-string credentials.  These are baked into the Nix store
      # (world-readable), so prefer apiKeyFile/apiSecretFile for anything
      # you would not print in public.
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

      # Keyfile credentials — e.g. agenix-managed paths such as
      # /run/secrets/kraken_api_key.  The file is read by the systemd
      # oneshot at activation time; its contents never enter /nix/store.
      apiKeyFile = lib.mkOption {
        type = lib.types.nullOr lib.types.path;
        default = null;
        description = ''
          Path to a file whose first line is the API key (e.g. an
          agenix-managed /run/secrets path).  Takes precedence over
          credentials.apiKey when both are set.
        '';
      };

      apiSecretFile = lib.mkOption {
        type = lib.types.nullOr lib.types.path;
        default = null;
        description = ''
          Path to a file whose first line is the API secret (e.g. an
          agenix-managed /run/secrets path).  Takes precedence over
          credentials.apiSecret when both are set.
        '';
      };

      # Path to an existing .env file (alternative to setting individual
      # credential options above).  When set, this takes precedence over
      # everything and no env file is generated.
      envFile = lib.mkOption {
        type = lib.types.nullOr lib.types.path;
        default = null;
        description = "Path to a .env file with KRAKEN_* variables.";
      };

      # systemd units the credential oneshot must wait for.  When using the
      # *File options with a secrets manager (agenix, sops-nix, ...), add
      # the unit(s) that materialize those files here so the env file is
      # not written (and fail) before the secrets exist.
      after = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ "agenix-activation.service" ];
        description = ''
          Units to order the credential writer after.  Defaults to
          agenix v1's activation unit; adjust for your secrets manager
          (e.g. sops-nix.service, agenix2's units, or individual
          age-<secret>.service units).
        '';
      };
    };

    # App-level (non-secret) KRAKEN_* configuration.  Kept separate from
    # credentials so code review does not have to wonder which values are
    # sensitive.  Mirrors the optional variables in .env.example.
    settings = {
      restUrl = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "KRAKEN_REST_URL override (REST base URL).";
      };

      wsUrl = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "KRAKEN_WS_URL override (public WebSocket v2 url).";
      };

      wsAuthUrl = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "KRAKEN_WS_AUTH_URL override (private WebSocket v2 url).";
      };

      minInterval = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "KRAKEN_MIN_INTERVAL minimum seconds between REST calls (e.g. \"0.08\").";
      };

      # Paper trading (KRAKEN_PAPER_*).  The simulation needs no credentials;
      # these just tune its defaults.
      paperBalance = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "KRAKEN_PAPER_BALANCE opening quote balance for paper trading (e.g. \"10000\").";
      };

      paperFeeTaker = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "KRAKEN_PAPER_FEE_TAKER fee rate for market fills (e.g. \"0.0026\").";
      };

      paperFeeMaker = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "KRAKEN_PAPER_FEE_MAKER fee rate for resting limit fills (e.g. \"0.0016\").";
      };

      paperPrice = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "KRAKEN_PAPER_PRICE fill trigger: \"book\" or \"last\".";
      };

      paperState = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "KRAKEN_PAPER_STATE JSON path persisting the paper account between runs.";
      };

      # Catch-all for any other KRAKEN_* variable (written verbatim).
      # Null values are skipped, which lets you explicitly clear an
      # inherited environment variable.
      extra = lib.mkOption {
        type = lib.types.attrsOf (lib.types.nullOr lib.types.str);
        default = { };
        description = "Extra KRAKEN_* environment variables (NAME = value).";
      };
    };
  };

  # ── Implementation ──────────────────────────────────────────────────────
  config = lib.mkIf cfg.enable (
    let
      writeEnv =
        cfg.credentials.envFile == null
        && (cfg.credentials.apiKey != ""
        || cfg.credentials.apiSecret != ""
        || cfg.credentials.apiKeyFile != null
        || cfg.credentials.apiSecretFile != null
        || cfg.settings.restUrl != null
        || cfg.settings.wsUrl != null
        || cfg.settings.wsAuthUrl != null
        || cfg.settings.minInterval != null
        || cfg.settings.paperBalance != null
        || cfg.settings.paperFeeTaker != null
        || cfg.settings.paperFeeMaker != null
        || cfg.settings.paperPrice != null
        || cfg.settings.paperState != null
        || cfg.settings.extra != { });

      # Individual env lines, in stable order: credentials first, then
      # settings, then catch-all extras.
      envLines =
        (lib.optional (cfg.credentials.apiKeyFile != null) (fileLine "KRAKEN_API_KEY" cfg.credentials.apiKeyFile))
        ++ lib.optional (cfg.credentials.apiKeyFile == null && cfg.credentials.apiKey != "") (valueLine "KRAKEN_API_KEY" cfg.credentials.apiKey)
        ++ lib.optional (cfg.credentials.apiSecretFile != null) (fileLine "KRAKEN_API_SECRET" cfg.credentials.apiSecretFile)
        ++ lib.optional (cfg.credentials.apiSecretFile == null && cfg.credentials.apiSecret != "") (valueLine "KRAKEN_API_SECRET" cfg.credentials.apiSecret)
        ++ lib.optional (cfg.settings.restUrl != null) (valueLine "KRAKEN_REST_URL" cfg.settings.restUrl)
        ++ lib.optional (cfg.settings.wsUrl != null) (valueLine "KRAKEN_WS_URL" cfg.settings.wsUrl)
        ++ lib.optional (cfg.settings.wsAuthUrl != null) (valueLine "KRAKEN_WS_AUTH_URL" cfg.settings.wsAuthUrl)
++ lib.optional (cfg.settings.minInterval != null) (valueLine "KRAKEN_MIN_INTERVAL" cfg.settings.minInterval)
  ++ lib.optional (cfg.settings.paperBalance != null) (valueLine "KRAKEN_PAPER_BALANCE" cfg.settings.paperBalance)
  ++ lib.optional (cfg.settings.paperFeeTaker != null) (valueLine "KRAKEN_PAPER_FEE_TAKER" cfg.settings.paperFeeTaker)
  ++ lib.optional (cfg.settings.paperFeeMaker != null) (valueLine "KRAKEN_PAPER_FEE_MAKER" cfg.settings.paperFeeMaker)
  ++ lib.optional (cfg.settings.paperPrice != null) (valueLine "KRAKEN_PAPER_PRICE" cfg.settings.paperPrice)
  ++ lib.optional (cfg.settings.paperState != null) (valueLine "KRAKEN_PAPER_STATE" cfg.settings.paperState)
  ++ lib.concatLists (lib.mapAttrsToList
          (name: value: lib.optional (value != null) (valueLine name value))
          cfg.settings.extra);
    in
    {
      # Make the package available system-wide.
      environment.systemPackages = [ cfg.package ];

      # Write a credentials file if individual options are provided.
      # The file is mode 0600 and owned by root, loadable by systemd and
      # by the app's dotenv loader.  Keyfile values are resolved here via
      # `cat`, so secrets only ever exist in /run and never in the store.
      systemd.services."kraken-python-env" = lib.mkIf writeEnv {
        description = "Write kraken-python credentials";
        wantedBy = [ "multi-user.target" ];
        after = cfg.credentials.after;
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
        };
        script = ''
          set -euo pipefail
          mkdir -p ${builtins.dirOf cfg.envFilePath}
          {
          ${lib.concatMapStringsSep "\n" (line: "  " + line)
            ([ "echo '# generated by services.kraken-python - do not edit'" ] ++ envLines)}
          } > ${cfg.envFilePath}
          chmod 0600 ${cfg.envFilePath}
        '';
      };
    }
  );
}
