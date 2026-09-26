# nix/checks.nix — evaluate the NixOS module against representative
# configurations and assert the generated env-writer behaves.
#
# Runs on `nix flake check` (or `nix build .#checks.<system>.kraken-module`).
# Called with: { lib, pkgs } = nixpkgs.legacyPackages.<system>.

{ lib, pkgs }:

let
  module = import ./module.nix;

  # Wrap the module in a bare evalModules.  The only NixOS option it
  # references is systemd.services, which we mock here as free-form.
  eval = modCfg: lib.evalModules {
    modules = [
      module
      ({ config, lib, ... }: {
        options.systemd.services = lib.mkOption {
          type = lib.types.attrsOf lib.types.anything;
          default = { };
        };
        options.environment.systemPackages = lib.mkOption {
          type = lib.types.listOf lib.types.package;
          default = [ ];
        };
        config = { services.kraken-python = modCfg; };
      })
    ];
    specialArgs = { pkgs = pkgs; };
  };

  services = modCfg: (eval modCfg).config.systemd.services;
  envWriter = modCfg:
    let svcs = services modCfg;
    in if svcs ? "kraken-python-env" then svcs."kraken-python-env" else null;
  scriptOf = modCfg:
    let w = envWriter modCfg;
    in if w == null then "" else w.script;

  afterOf = modCfg:
    let w = envWriter modCfg;
    in if w == null then [ ] else w.after;

  cfgKeyfile = {
    enable = true;
    package = pkgs.hello;
    credentials = {
      apiKeyFile = "/run/secrets/kraken_api_key";
      apiSecretFile = "/run/secrets/kraken_api_secret";
    };
  };

  cfgString = {
    enable = true;
    package = pkgs.hello;
    credentials = {
      apiKey = "litkey_9x";
      apiSecret = "litsrc_8y";
    };
  };

  cfgSettings = {
    enable = true;
    package = pkgs.hello;
    credentials.apiKeyFile = "/run/secrets/kraken_api_key";
    settings = {
      restUrl = "https://api.example";
      wsUrl = "wss://ws.example/v2";
      wsAuthUrl = "wss://ws-auth.example/v2";
      minInterval = "0.1";
      extra = {
        KRAKEN_MARKET = "btc";
        KRAKEN_UNSET = null; # must be skipped
      };
    };
  };

  cfgEnvFileOnly = {
    enable = true;
    package = pkgs.hello;
    credentials.envFile = "/etc/kraken/.env";
  };

  # Paper settings alone must trigger the env writer (no credentials).
  cfgPaperOnly = {
    enable = true;
    package = pkgs.hello;
    settings = {
      paperBalance = "5000";
      paperFeeTaker = "0.0026";
      paperFeeMaker = "0.0016";
      paperPrice = "last";
      paperState = "/var/lib/kraken-paper/state.json";
    };
  };

  cfgDisabled = {
    enable = false;
    package = pkgs.hello;
    credentials.apiKey = "NOPE_9x";
  };

  cfgCustomAfter = cfgKeyfile // {
    credentials = cfgKeyfile.credentials // { after = [ "sops-nix.service" ]; };
  };

  join = builtins.concatStringsSep ",";
in
pkgs.runCommand "kraken-python-module-checks"
{
  keyfile = scriptOf cfgKeyfile;
  stringC = scriptOf cfgString;
  settingsC = scriptOf cfgSettings;
  envfileC = scriptOf cfgEnvFileOnly;
  disabledC = scriptOf cfgDisabled;
  paperC = scriptOf cfgPaperOnly;
  afterDefault = join (afterOf cfgKeyfile);
  afterCustom = join (afterOf cfgCustomAfter);
} ''
  set -euo pipefail

  # Keyfile credentials are resolved at runtime via $(cat ...); the literal
  # keyfile path appears, and nothing of the secret does.
  grep -Fq 'KRAKEN_API_KEY=$(cat' <<<"$keyfile"
  grep -Fq 'KRAKEN_API_SECRET=$(cat' <<<"$keyfile"
  grep -Fq 'kraken_api_key' <<<"$keyfile"
  if grep -Fq 'litkey_9x' <<<"$keyfile"; then
    echo "FAIL: keyfile config must not embed the plain string value"; exit 1; fi

  # Plain-string credentials are written literally (echo), never via cat.
  grep -Fq 'litkey_9x' <<<"$stringC"
  grep -Fq 'litsrc_8y' <<<"$stringC"
  if grep -Fq '$(cat' <<<"$stringC"; then
    echo "FAIL: string config must not use \$(cat)"; exit 1; fi

  # Settings + catch-all extras land verbatim; null extras are skipped.
  grep -Fq 'https://api.example' <<<"$settingsC"
  grep -Fq 'wss://ws.example/v2' <<<"$settingsC"
  grep -Fq 'KRAKEN_MIN_INTERVAL' <<<"$settingsC"
  grep -Fq '0.1' <<<"$settingsC"
  grep -Fq 'KRAKEN_MARKET' <<<"$settingsC"
  grep -Fq 'btc' <<<"$settingsC"
  if grep -Fq 'KRAKEN_UNSET' <<<"$settingsC"; then
    echo "FAIL: null extra var must be skipped"; exit 1; fi

  # Paper settings alone trigger the env writer and land verbatim.
  grep -Fq 'KRAKEN_PAPER_BALANCE=5000' <<<"$paperC"
  grep -Fq 'KRAKEN_PAPER_FEE_TAKER=0.0026' <<<"$paperC"
  grep -Fq 'KRAKEN_PAPER_FEE_MAKER=0.0016' <<<"$paperC"
  grep -Fq 'KRAKEN_PAPER_PRICE=last' <<<"$paperC"
  grep -Fq 'KRAKEN_PAPER_STATE=/var/lib/kraken-paper/state.json' <<<"$paperC"

  # envFile / disabled configs must not generate an env writer at all.
  if [ -n "$envfileC" ]; then
    echo "FAIL: envFile should suppress the env writer"; exit 1; fi
  if [ -n "$disabledC" ]; then
    echo "FAIL: enable=false should suppress the env writer"; exit 1; fi

  # Ordering defaults to agenix v1's activation unit and honours overrides.
  if [ "$afterDefault" != "agenix-activation.service" ]; then
    echo "FAIL: default after = $afterDefault"; exit 1; fi
  if [ "$afterCustom" != "sops-nix.service" ]; then
    echo "FAIL: custom after = $afterCustom"; exit 1; fi

  touch "$out"
''
