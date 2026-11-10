/**
 * Where a postcode area sits on the map: a small lookup of area centroids (code|name|lat|lon).
 * The atlas places each tile by these, on a coarse grid; an area that is not listed goes in a tidy
 * row below the map, so no area is ever dropped. Only the area (UK letters, US first three ZIP
 * digits) is used, never an address.
 */
const UK = `AB|Aberdeen|57.15|-2.10;AL|St Albans|51.75|-0.34;B|Birmingham|52.48|-1.90;BA|Bath|51.38|-2.36;BB|Blackburn|53.75|-2.48;BD|Bradford|53.80|-1.75;BH|Bournemouth|50.72|-1.88;BL|Bolton|53.58|-2.43;BN|Brighton|50.83|-0.14;BR|Bromley|51.40|0.02;BS|Bristol|51.45|-2.59;BT|Belfast|54.60|-5.93;CA|Carlisle|54.89|-2.93;CB|Cambridge|52.20|0.12;CF|Cardiff|51.48|-3.18;CH|Chester|53.20|-2.90;CM|Chelmsford|51.73|0.47;CO|Colchester|51.89|0.90;CR|Croydon|51.37|-0.10;CT|Canterbury|51.28|1.08;CV|Coventry|52.41|-1.51;CW|Crewe|53.10|-2.44;DA|Dartford|51.44|0.22;DD|Dundee|56.46|-2.97;DE|Derby|52.92|-1.48;DG|Dumfries|55.07|-3.61;DH|Durham|54.78|-1.57;DL|Darlington|54.52|-1.55;DN|Doncaster|53.52|-1.13;DT|Dorchester|50.71|-2.44;DY|Dudley|52.51|-2.09;E|East London|51.54|-0.02;EC|City of London|51.52|-0.09;EH|Edinburgh|55.95|-3.19;EN|Enfield|51.65|-0.08;EX|Exeter|50.72|-3.53;FK|Falkirk|56.00|-3.78;FY|Blackpool|53.82|-3.05;G|Glasgow|55.86|-4.25;GL|Gloucester|51.86|-2.24;GU|Guildford|51.24|-0.57;HA|Harrow|51.58|-0.34;HD|Huddersfield|53.65|-1.78;HG|Harrogate|54.00|-1.54;HP|Hemel Hempstead|51.75|-0.74;HR|Hereford|52.06|-2.72;HU|Hull|53.74|-0.34;HX|Halifax|53.72|-1.86;IG|Ilford|51.57|0.07;IP|Ipswich|52.06|1.15;IV|Inverness|57.48|-4.22;KA|Kilmarnock|55.61|-4.50;KT|Kingston|51.40|-0.30;KY|Kirkcaldy|56.20|-3.15;L|Liverpool|53.41|-2.98;LA|Lancaster|54.05|-2.80;LD|Llandrindod Wells|52.24|-3.38;LE|Leicester|52.63|-1.13;LL|Llandudno|53.10|-3.80;LN|Lincoln|53.23|-0.54;LS|Leeds|53.80|-1.55;LU|Luton|51.88|-0.42;M|Manchester|53.48|-2.24;ME|Medway|51.35|0.55;MK|Milton Keynes|52.04|-0.76;ML|Motherwell|55.78|-3.98;N|North London|51.59|-0.11;NE|Newcastle|54.98|-1.61;NG|Nottingham|52.95|-1.15;NN|Northampton|52.24|-0.90;NP|Newport|51.59|-3.00;NR|Norwich|52.63|1.30;NW|North West London|51.55|-0.18;OL|Oldham|53.54|-2.11;OX|Oxford|51.75|-1.26;PA|Paisley|55.85|-4.43;PE|Peterborough|52.57|-0.24;PH|Perth|56.40|-3.43;PL|Plymouth|50.37|-4.14;PO|Portsmouth|50.80|-1.09;PR|Preston|53.76|-2.70;RG|Reading|51.45|-1.00;RH|Redhill|51.20|-0.17;RM|Romford|51.56|0.18;S|Sheffield|53.38|-1.47;SA|Swansea|51.62|-3.94;SE|South East London|51.48|-0.05;SG|Stevenage|51.90|-0.20;SK|Stockport|53.40|-2.15;SL|Slough|51.51|-0.60;SM|Sutton|51.37|-0.19;SN|Swindon|51.56|-1.78;SO|Southampton|50.90|-1.40;SP|Salisbury|51.07|-1.80;SR|Sunderland|54.90|-1.38;SS|Southend|51.55|0.70;ST|Stoke|53.00|-2.18;SW|South West London|51.46|-0.15;SY|Shrewsbury|52.70|-2.75;TA|Taunton|51.02|-3.10;TD|Borders|55.60|-2.80;TF|Telford|52.68|-2.45;TN|Tunbridge Wells|51.13|0.30;TQ|Torquay|50.46|-3.53;TR|Truro|50.26|-5.05;TS|Teesside|54.57|-1.23;TW|Twickenham|51.45|-0.35;UB|Uxbridge|51.54|-0.42;W|West London|51.51|-0.20;WA|Warrington|53.39|-2.59;WC|Central London|51.52|-0.12;WD|Watford|51.65|-0.40;WF|Wakefield|53.68|-1.50;WN|Wigan|53.54|-2.63;WR|Worcester|52.19|-2.22;WS|Walsall|52.58|-1.98;WV|Wolverhampton|52.59|-2.13;YO|York|53.96|-1.08`;

const US = `100|New York|40.75|-73.99;112|Brooklyn|40.65|-73.95;021|Boston|42.36|-71.06;191|Philadelphia|39.95|-75.17;200|Washington DC|38.90|-77.04;152|Pittsburgh|40.44|-80.00;282|Charlotte|35.23|-80.84;303|Atlanta|33.75|-84.39;322|Jacksonville|30.33|-81.66;331|Miami|25.76|-80.19;372|Nashville|36.16|-86.78;432|Columbus|39.96|-83.00;441|Cleveland|41.50|-81.69;462|Indianapolis|39.77|-86.16;481|Detroit|42.33|-83.05;530|Milwaukee|43.04|-87.91;554|Minneapolis|44.98|-93.27;606|Chicago|41.88|-87.63;631|St Louis|38.63|-90.20;641|Kansas City|39.10|-94.58;701|New Orleans|29.95|-90.07;750|Dallas|32.78|-96.80;770|Houston|29.76|-95.37;787|Austin|30.27|-97.74;802|Denver|39.74|-104.99;841|Salt Lake City|40.76|-111.89;852|Phoenix|33.45|-112.07;891|Las Vegas|36.17|-115.14;900|Los Angeles|34.05|-118.24;921|San Diego|32.72|-117.16;941|San Francisco|37.77|-122.42;958|Sacramento|38.58|-121.49;972|Portland|45.52|-122.68;981|Seattle|47.61|-122.33`;

export type Region = "GB" | "US";
export type Geo = { code: string; name: string; lat: number; lon: number };

const parse = (t: string) => new Map(t.split(";").map((r) => {
  const [code, name, lat, lon] = r.split("|");
  return [code, { code, name, lat: Number(lat), lon: Number(lon) }] as const;
}));
const TABLE: Record<Region, Map<string, Geo>> = { GB: parse(UK), US: parse(US) };

/** The area of a district: "SW9" gives "SW", "M14" gives "M", ZIP "10003" gives "100". */
export function areaOf(district: string, region: Region): string {
  const d = district.trim().toUpperCase();
  return region === "US" ? d.replace(/\D/g, "").slice(0, 3) : (/^[A-Z]{1,2}/.exec(d)?.[0] ?? d);
}

export const lookup = (area: string, region: Region): Geo | undefined => TABLE[region].get(area);
export const regionOf = (country: string): Region => (country.trim().toUpperCase() === "US" ? "US" : "GB");

export type Cell = { col: number; row: number; placed: boolean };

/**
 * One grid cell per area. The map is a plain equirectangular grid (a cell is 0.6 degrees across in
 * the UK, 2.2 in the US); two areas wanting one cell (London's eight) spread to the nearest free
 * ones, biggest first so the busiest keeps its true spot. Unlisted areas fill rows below the map.
 */
export function layout(items: { area: string; weight: number }[], region: Region): Map<string, Cell> {
  const step = region === "US" ? 2.2 : 0.6;
  const kx = Math.cos(((region === "US" ? 39 : 54) * Math.PI) / 180);
  const byWeight = (a: { area: string; weight: number }, b: { area: string; weight: number }) =>
    b.weight - a.weight || a.area.localeCompare(b.area);
  const known = items.filter((i) => lookup(i.area, region)).sort(byWeight);
  const geo = known.map((i) => lookup(i.area, region)!);
  const x0 = Math.min(...geo.map((g) => g.lon * kx)), y1 = Math.max(...geo.map((g) => g.lat));
  const taken = new Set<string>();
  const out = new Map<string, Cell>();
  known.forEach((i, n) => {
    const g = geo[n];
    const c0 = Math.round((g.lon * kx - x0) / step), r0 = Math.round((y1 - g.lat) / step);
    let best = { col: c0, row: r0 };
    for (let d = 0; d < 16; d++) {
      let pick: { col: number; row: number; dist: number } | null = null;
      for (let dc = -d; dc <= d; dc++) {
        for (let dr = -d; dr <= d; dr++) {
          if (Math.max(Math.abs(dc), Math.abs(dr)) !== d) continue;
          const col = c0 + dc, row = r0 + dr;
          if (col < 0 || row < 0 || taken.has(`${col}:${row}`)) continue;
          const dist = dc * dc + dr * dr;
          if (!pick || dist < pick.dist) pick = { col, row, dist };
        }
      }
      if (pick) { best = pick; break; }
    }
    taken.add(`${best.col}:${best.row}`);
    out.set(i.area, { col: best.col, row: best.row, placed: true });
  });
  const rows = out.size ? Math.max(...[...out.values()].map((c) => c.row)) + 2 : 0;
  const PER_ROW = 8;
  items.filter((i) => !lookup(i.area, region)).sort(byWeight)
    .forEach((i, n) => out.set(i.area, { col: n % PER_ROW, row: rows + Math.floor(n / PER_ROW), placed: false }));
  return out;
}
