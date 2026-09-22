{
  description = "kraken-python — package, module, and dev environment";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" "x86_64-darwin" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    in
    {
      # ── Packages ──────────────────────────────────────────────────────────
      # Exposes the Python app as `nix build .#kraken-python`
      # and as the default package.
      packages = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          kraken-pkg = pkgs.callPackage ./nix/default.nix { };
        in
        {
          default = kraken-pkg;
          kraken-python = kraken-pkg;
        }
      );

      # ── NixOS Module ──────────────────────────────────────────────────────
      # Import in your NixOS config:
      #
      #   inputs.kraken-python.url = "github:Cairnstew/kraken-python";
      #
      #   imports = [ inputs.kraken-python.nixosModules.default ];
      #   services.kraken-python.enable = true;
      #
      nixosModules.default = import ./nix/module.nix;

      # ── Checks ───────────────────────────────────────────────────────────
      # `nix flake check` evaluates the NixOS module against representative
      # configurations and asserts the generated env-writer behaves
      # (keyfile vs plain credentials, settings, ordering).
      checks = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          kraken-module = pkgs.callPackage ./nix/checks.nix { };
        }
      );

      # ── Dev Shell ─────────────────────────────────────────────────────────
      # `nix develop` drops you into a shell with Python, requests,
      # websocket-client, and pytest on PATH.
      devShells = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python3.withPackages (ps: with ps; [
            requests
            websocket-client
            python-dotenv
            pytest
          ]);
        in
        {
          default = pkgs.mkShell {
            packages = [ python ];
            shellHook = ''
              echo "kraken-python dev shell"
              python -c 'import kraken_api; print("kraken_api:", kraken_api.__version__)'
              python -c 'import requests, websocket, dotenv; print("deps: requests, websocket-client, python-dotenv")'
            '';
          };
        }
      );
    };
}
