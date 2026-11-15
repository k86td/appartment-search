{
  description = "Scraper de sites de logement + interface Datasette";

  inputs = {
    # Épinglé sur 25.11 : dans 26.05/unstable, asgi-csrf (dépendance de datasette)
    # est marqué broken avec python-multipart >= 0.0.26.
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.11";
  };

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);

      perSystem = system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python3;
        py = python.pkgs;

        datasette-cluster-map = py.buildPythonPackage rec {
          pname = "datasette_cluster_map";
          version = "0.19";
          format = "wheel";
          src = py.fetchPypi {
            inherit pname version format;
            dist = "py3";
            python = "py3";
            hash = "sha256-5wziCDIrF29rKBJv8Zu39VHFg0F2+opMQ5D8PnGk03k=";
          };
          dependencies = [ py.datasette ];
          doCheck = false;
        };

        appartment-search = py.buildPythonApplication {
          pname = "appartment-search";
          version = "0.1.0";
          pyproject = true;
          src = ./.;
          build-system = [ py.hatchling ];
          dependencies = [ py.requests ];
        };

        datasetteEnv = python.withPackages (ps: [ ps.datasette datasette-cluster-map ]);

        # Lance Datasette sur listings.db du dossier courant, avec le metadata.yml du repo.
        ui = pkgs.writeShellApplication {
          name = "appartment-search-ui";
          runtimeInputs = [ datasetteEnv ];
          text = ''
            exec datasette "''${DB:-listings.db}" -m ${./metadata.yml} "$@"
          '';
        };
      in
      {
        packages = {
          default = appartment-search;
          inherit appartment-search ui;
        };

        apps = {
          default = { type = "app"; program = "${appartment-search}/bin/appartment-search"; };
          ui = { type = "app"; program = "${ui}/bin/appartment-search-ui"; };
        };

        devShells.default = pkgs.mkShell {
          packages = [ (python.withPackages (ps: [ ps.requests ps.datasette datasette-cluster-map ])) ];
        };
      };
    in
    {
      packages = forAllSystems (s: (perSystem s).packages);
      apps = forAllSystems (s: (perSystem s).apps);
      devShells = forAllSystems (s: (perSystem s).devShells);
    };
}
