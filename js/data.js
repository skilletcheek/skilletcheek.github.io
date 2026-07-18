/* =========================================================================
 *  RJ Does Dallas — data & configuration
 *  -------------------------------------------------------------------------
 *  Everything a non-coder needs to tweak lives at the top of this file.
 *  ========================================================================= */

/* ---- SITE CONFIG -------------------------------------------------------- */
const CONFIG = {
  siteName: "RJ Does Dallas",
  tagline: "Your daily guide to Dallas–Fort Worth",

  /* Live event sources. Fill these in AFTER you deploy (see README).
     Leave blank to run on curated + JSON data only — the site still works. */
  ticketmasterApiKey: "1TMfuA687o9YVX8gTnBmVIvzWZ6s1VGh", // https://developer.ticketmaster.com
  seatgeekClientId: "NTk2OTQ4OTV8MTc4NDMxODM2OC4xMjA3NDc2", // https://seatgeek.com/account/develop
  predicthqToken: "",            // optional: https://www.predicthq.com (paid)
  googleSheetCsvUrl: "",         // optional: a "Publish to web -> CSV" URL
  eventsJsonUrl: "events.json",  // simple file you can hand-edit; ships with the site
  aggregatedJsonUrl: "live-events.json", // written nightly by the GitHub Action (see scripts/)

  /* Where newsletter signups, event submissions and advertising inquiries go
     when no form endpoint is configured (opens the visitor's email app). */
  contactEmail: "richard.jenkins@student.uagc.edu",

  /* DFW search box for live APIs */
  geo: { lat: 32.7767, lng: -96.7970, radiusMiles: 40 },

  /* Monetization endpoints (optional — forms degrade gracefully if blank) */
  newsletterEndpoint: "",        // e.g. a Formspree or Mailchimp form-action URL
  submitEventEndpoint: "",       // e.g. a Formspree URL for the "Submit an event" form
  affiliateTag: "",              // appended to outbound ticket links, if your program uses one
  adsEnabled: false,             // flip true once an ad network (AdSense/Ezoic) is approved
};

/* ---- CATEGORIES --------------------------------------------------------- */
const CATEGORIES = {
  music:     { label: "Live Music",       color: "#ff5d8f", emoji: "🎸" },
  food:      { label: "Food & Drink",     color: "#ffb020", emoji: "🍽️" },
  arts:      { label: "Arts & Museums",   color: "#a78bfa", emoji: "🎨" },
  outdoors:  { label: "Outdoors & Parks", color: "#22d3a6", emoji: "🌳" },
  sports:    { label: "Sports",           color: "#4f8cff", emoji: "🏟️" },
  family:    { label: "Family & Kids",    color: "#38bdf8", emoji: "🧸" },
  market:    { label: "Markets",          color: "#f97316", emoji: "🛍️" },
  nightlife: { label: "Nightlife",        color: "#c084fc", emoji: "🌙" },
  festival:  { label: "Festivals",        color: "#f43f5e", emoji: "🎪" },
};

/* ---- SPONSORED / FEATURED LISTINGS -------------------------------------
 *  These get pinned to the top with a gold "Sponsored" badge. This is your
 *  #1 revenue product — sell a pinned spot to a local venue or event.
 *
 *  HOW TO SELL ONE (copy the template below):
 *    • Suggested pricing: $50–$150/mo for a category-day pin, $150–$300/mo
 *      for an always-on top spot. Charge more for weekends.
 *    • `sponsorUntil` auto-expires the placement (YYYY-MM-DD) so you never
 *      have to remember to pull it down when their month ends.
 *    • `recur` controls which days it shows (same rules as ACTIVITIES below).
 *
 *  TEMPLATE — duplicate, fill in, and put a paying advertiser here:
 *    { name: "Grand Opening — Trinity Groves Night Market", cat: "market",
 *      area: "West Dallas", recur: { weekly: [5,6] }, time: "5:00 PM – 11:00 PM",
 *      cost: 0, sponsor: "Trinity Groves", sponsorUntil: "2026-09-01",
 *      desc: "60+ vendors, live DJs and skyline views across the bridge.",
 *      url: "https://trinitygroves.com" },
 *
 *  The entry below is a "house ad": it fills the sponsored slot while you have
 *  no paying advertiser, turning empty inventory into a sales pitch. Delete it
 *  the moment a real sponsor takes the spot.
 * ------------------------------------------------------------------------ */
const SPONSORED = [
  { name: "Promote your event right here", cat: "festival", area: "Dallas–Fort Worth",
    recur: { daily: true }, time: "Featured placement", cost: 0,
    sponsor: "Advertise with us",
    desc: "Own the top of RJ Does Dallas. Pin your venue, market, or event to the #1 spot with a gold badge — seen by everyone browsing that day. Tap Details to get started.",
    url: "#advertise" },
];

/* ---- CURATED RECURRING ACTIVITIES --------------------------------------
   recur types:
     daily:true                        -> every day
     weekly:[0-6]                      -> weekdays (0=Sun ... 6=Sat)
     monthly:{week,day}                -> e.g. {week:1,day:0} = 1st Sunday
     dateRange:{start,end,weekly?}     -> seasonal "MM-DD" window
   -------------------------------------------------------------------------- */
const ACTIVITIES = [
  // Markets
  { name: "Dallas Farmers Market", cat: "market", area: "Downtown Dallas",
    recur: { weekly: [6,0] }, time: "9:00 AM – 5:00 PM", cost: 0,
    desc: "The Shed's open-air stalls plus The Market food hall — produce, tacos, coffee and local makers.",
    url: "https://dallasfarmersmarket.org" },
  { name: "Cowtown Farmers Market", cat: "market", area: "Fort Worth",
    recur: { weekly: [6] }, time: "8:00 AM – 12:00 PM", cost: 0,
    desc: "Year-round Saturday market with Texas-grown produce, grass-fed meats and fresh eggs.",
    url: "https://cowtownfarmersmarket.org" },
  { name: "Coppell Farmers Market", cat: "market", area: "Coppell",
    recur: { dateRange: { start: "04-01", end: "11-30", weekly: [6] } }, time: "8:00 AM – 12:00 PM", cost: 0,
    desc: "Seasonal producers-only market with live music and a kids' corner most Saturdays.",
    url: "https://coppellfarmersmarket.org" },
  { name: "Sundance Square Makers Market", cat: "market", area: "Fort Worth",
    recur: { weekly: [0] }, time: "11:00 AM – 4:00 PM", cost: 0,
    desc: "Sunday makers market in Sundance Square with artisans, vintage and street food.",
    url: "https://www.sundancesquare.com" },

  // Arts & Museums
  { name: "Dallas Museum of Art", cat: "arts", area: "Arts District, Dallas",
    recur: { weekly: [2,3,4,5,6,0] }, time: "11:00 AM – 5:00 PM", cost: 0,
    desc: "General admission is free. Rotating special exhibitions and a great café.",
    url: "https://dma.org" },
  { name: "Kimbell Art Museum", cat: "arts", area: "Fort Worth Cultural District",
    recur: { weekly: [2,3,4,5,6,0] }, time: "10:00 AM – 5:00 PM", cost: 0,
    desc: "Louis Kahn masterpiece building; free permanent collection, ticketed special shows.",
    url: "https://kimbellart.org" },
  { name: "The Modern — Free First Sunday", cat: "arts", area: "Fort Worth Cultural District",
    recur: { monthly: { week: 1, day: 0 } }, time: "11:00 AM – 5:00 PM", cost: 0,
    desc: "Admission is free on the first Sunday of every month at the Modern Art Museum.",
    url: "https://themodern.org" },
  { name: "Nasher Sculpture Center", cat: "arts", area: "Arts District, Dallas",
    recur: { weekly: [2,3,4,5,6,0] }, time: "11:00 AM – 5:00 PM", cost: 10,
    desc: "Indoor galleries opening onto a sculpture garden in the heart of the Arts District.",
    url: "https://nashersculpturecenter.org" },

  // Outdoors
  { name: "Klyde Warren Park", cat: "outdoors", area: "Downtown Dallas",
    recur: { daily: true }, time: "6:00 AM – 11:00 PM", cost: 0,
    desc: "Deck park over the freeway — food trucks, free fitness classes, games and a kids' zone.",
    url: "https://klydewarrenpark.org" },
  { name: "Fort Worth Botanic Garden", cat: "outdoors", area: "Fort Worth",
    recur: { daily: true }, time: "8:00 AM – 6:00 PM", cost: 12,
    desc: "Texas' oldest botanic garden — Japanese Garden, rose gardens and a rainforest conservatory.",
    url: "https://fwbg.org" },
  { name: "Katy Trail Walk & Ride", cat: "outdoors", area: "Uptown Dallas",
    recur: { daily: true }, time: "Sunrise – Sunset", cost: 0,
    desc: "3.5-mile urban rail-trail for walking, running and biking through Uptown.",
    url: "https://katytraildallas.org" },
  { name: "White Rock Lake Loop", cat: "outdoors", area: "East Dallas",
    recur: { daily: true }, time: "Sunrise – Sunset", cost: 0,
    desc: "9.3-mile lakeside trail with skyline views, the Bath House arts center and a spillway.",
    url: "https://whiterocklake.org" },
  { name: "Trinity River Kayaking", cat: "outdoors", area: "Fort Worth",
    recur: { dateRange: { start: "04-15", end: "10-15", weekly: [5,6,0] } }, time: "10:00 AM – 6:00 PM", cost: 35,
    desc: "Paddle the Trinity through the greenbelt — rentals available on weekends in season.",
    url: "https://trinityrivervision.org" },

  // Live music & nightlife
  { name: "Live Jazz at Sandaga 813", cat: "music", area: "Deep Ellum, Dallas",
    recur: { weekly: [4] }, time: "8:00 PM – 11:00 PM", cost: 10,
    desc: "Thursday-night jazz sessions in Deep Ellum's arts corridor.",
    url: "https://deepellumtexas.com" },
  { name: "The Balcony Club — Live Jazz & Blues", cat: "music", area: "Lakewood, East Dallas",
    recur: { daily: true }, time: "9:30 PM – 12:30 AM", cost: null,
    desc: "Live jazz and blues seven nights a week in the moody little listening room beside the Lakewood Theater. Cover varies by night — often none.",
    url: "https://www.balconyclub.com" },
  { name: "Adair's Saloon — Honky-Tonk Nightly", cat: "music", area: "Deep Ellum, Dallas",
    recur: { daily: true }, time: "8:00 PM – 1:00 AM", cost: 0,
    desc: "Free live country and outlaw honky-tonk seven nights a week at the graffiti-covered Deep Ellum dive — kitchen slings famous burgers till 1:30 AM.",
    url: "https://www.adairssaloon.com" },
  { name: "The Free Man — Live Jazz & Funk", cat: "music", area: "Deep Ellum, Dallas",
    recur: { daily: true }, time: "7:00 PM – 12:00 AM", cost: 0,
    desc: "Cajun kitchen with live jazz, funk and brass bands nightly — no cover, two stages on weekends.",
    url: "https://freemandallas.com" },
  { name: "Revelers Hall — Live Jazz Daily", cat: "music", area: "Bishop Arts, Oak Cliff",
    recur: { daily: true }, time: "Sets daily — see calendar", cost: null,
    desc: "New Orleans-style jazz bar in Bishop Arts with live bands every single day — small per-seat band fee goes straight to the musicians.",
    url: "https://www.revelershall.com" },
  { name: "Swing Night at Sons of Hermann Hall", cat: "music", area: "Deep Ellum, Dallas",
    recur: { weekly: [3] }, time: "8:00 PM – 11:00 PM", cost: 10,
    desc: "Beginner swing lesson then open dancing on the spring-loaded floor of the 1910 fraternal hall — a Dallas institution.",
    url: "https://www.sonsofhermann.com" },
  { name: "Billy Bob's Texas — Live Country", cat: "music", area: "Fort Worth Stockyards",
    recur: { weekly: [5,6] }, time: "7:00 PM – late", cost: 20,
    desc: "The world's largest honky-tonk: live country acts and pro bull riding on weekends.",
    url: "https://billybobstexas.com" },
  { name: "Sundance Square Live", cat: "music", area: "Downtown Fort Worth",
    recur: { weekly: [5,6] }, time: "6:00 PM – 10:00 PM", cost: 0,
    desc: "Free outdoor concerts on the plaza most weekend evenings, weather permitting.",
    url: "https://www.sundancesquare.com" },
  { name: "Deep Ellum Live Music Crawl", cat: "nightlife", area: "Deep Ellum, Dallas",
    recur: { weekly: [5,6] }, time: "9:00 PM – 2:00 AM", cost: 0,
    desc: "Dozens of live-music venues, breweries and murals within a few walkable blocks.",
    url: "https://deepellumtexas.com" },
  { name: "Trivia Night at Community Beer Co.", cat: "nightlife", area: "Design District, Dallas",
    recur: { weekly: [2] }, time: "7:00 PM – 9:00 PM", cost: 0,
    desc: "Tuesday pub trivia with local brews in the Design District taproom.",
    url: "https://communitybeer.com" },

  // Food & drink
  { name: "Truck Yard Beer Garden", cat: "food", area: "Lower Greenville, Dallas",
    recur: { daily: true }, time: "4:00 PM – 12:00 AM", cost: 0,
    desc: "Rotating food trucks, a treehouse bar and live music — dog- and kid-friendly.",
    url: "https://thetruckyard.com" },
  { name: "Bishop Arts Food Stroll", cat: "food", area: "Oak Cliff, Dallas",
    recur: { weekly: [5,6,0] }, time: "11:00 AM – 9:00 PM", cost: 0,
    desc: "Walkable district of indie restaurants, bakeries and patios in North Oak Cliff.",
    url: "https://bishopartsdistrict.com" },
  { name: "Rahr & Sons Brewery Tour", cat: "food", area: "Near Southside, Fort Worth",
    recur: { weekly: [4,5,6] }, time: "1:00 PM – 8:00 PM", cost: 15,
    desc: "Taproom pours and weekend tours at one of Fort Worth's original craft breweries.",
    url: "https://rahrbrewing.com" },

  // Sports (seasonal)
  { name: "Texas Rangers Baseball", cat: "sports", area: "Arlington",
    recur: { dateRange: { start: "04-01", end: "09-30", weekly: [1,2,3,4,5,6,0] } }, time: "Evening (check schedule)", cost: 25,
    desc: "MLB action under the retractable roof at Globe Life Field. Home games most weeks in season.",
    url: "https://mlb.com/rangers" },
  { name: "FC Dallas Match", cat: "sports", area: "Frisco",
    recur: { dateRange: { start: "02-20", end: "10-31", weekly: [6] } }, time: "7:30 PM", cost: 30,
    desc: "MLS soccer at Toyota Stadium — most home matches land on Saturday nights.",
    url: "https://fcdallas.com" },
  { name: "Dallas Mavericks Basketball", cat: "sports", area: "Downtown Dallas",
    recur: { dateRange: { start: "10-20", end: "04-15", weekly: [1,3,5,6,0] } }, time: "7:30 PM", cost: 40,
    desc: "NBA basketball at the American Airlines Center during the winter season.",
    url: "https://mavs.com" },

  // Family
  { name: "Dallas Zoo", cat: "family", area: "Oak Cliff, Dallas",
    recur: { daily: true }, time: "9:00 AM – 4:00 PM", cost: 20,
    desc: "106-acre zoo with a giraffe feeding deck and the Wilds of Africa monorail.",
    url: "https://dallaszoo.com" },
  { name: "Fort Worth Stockyards Cattle Drive", cat: "family", area: "Fort Worth Stockyards",
    recur: { daily: true }, time: "11:30 AM & 4:00 PM", cost: 0,
    desc: "Twice-daily longhorn cattle drive down Exchange Avenue — free to watch.",
    url: "https://fortworthstockyards.org" },
  { name: "Story Time at Half Price Books Flagship", cat: "family", area: "Northwest Dallas",
    recur: { weekly: [6] }, time: "11:00 AM", cost: 0,
    desc: "Free Saturday children's story time at the enormous flagship bookstore.",
    url: "https://hpb.com" },
  { name: "LEGOLAND Discovery Center", cat: "family", area: "Grapevine",
    recur: { daily: true }, time: "10:00 AM – 6:00 PM", cost: 25,
    desc: "Indoor LEGO play zones, rides and a 4D cinema at Grapevine Mills.",
    url: "https://legolanddiscoverycenter.com/dallas-fort-worth" },
];

/* ---- DISTRICTS (radar map + hub pages) ----------------------------------
   Approximate positions on the 800x520 radar canvas; `match` strings are
   tested against each event's `area` (lowercased). Additive config only —
   event content above is untouched. ------------------------------------- */
const DISTRICTS = [
  { slug: "downtown-dallas",  label: "Downtown Dallas",   x: 588, y: 318, match: ["downtown dallas", "victory park"] },
  { slug: "deep-ellum",       label: "Deep Ellum",        x: 626, y: 322, match: ["deep ellum"] },
  { slug: "arts-district",    label: "Arts District",     x: 598, y: 300, match: ["arts district"] },
  { slug: "uptown",           label: "Uptown",            x: 578, y: 288, match: ["uptown"] },
  { slug: "bishop-arts",      label: "Bishop Arts",       x: 562, y: 356, match: ["oak cliff", "bishop arts"] },
  { slug: "design-district",  label: "Design District",   x: 552, y: 300, match: ["design district"] },
  { slug: "lower-greenville", label: "Lower Greenville",  x: 614, y: 280, match: ["lower greenville", "east dallas"] },
  { slug: "fort-worth",       label: "Fort Worth",        x: 150, y: 318, match: ["fort worth", "southside"] },
  { slug: "stockyards",       label: "The Stockyards",    x: 142, y: 258, match: ["stockyards"] },
  { slug: "arlington",        label: "Arlington",         x: 330, y: 352, match: ["arlington"] },
  { slug: "grapevine",        label: "Grapevine",         x: 356, y: 190, match: ["grapevine"] },
  { slug: "irving",           label: "Irving",            x: 440, y: 272, match: ["irving", "las colinas"] },
  { slug: "frisco",           label: "Frisco",            x: 560, y: 96,  match: ["frisco"] },
  { slug: "plano",            label: "Plano",             x: 610, y: 150, match: ["plano", "coppell", "addison", "richardson", "northwest dallas"] },
  { slug: "mckinney",         label: "McKinney",          x: 676, y: 78,  match: ["mckinney", "allen"] },
];

/* ---- CURATED DISTRICT ITINERARIES (numbered evening flows) ------------- */
const ITINERARIES = [
  { district: "Deep Ellum", title: "The Deep Ellum Night",
    steps: [
      { time: "6:30 PM", title: "Dinner on Main Street", note: "Pick a patio among the murals — walkable blocks of indie kitchens." },
      { time: "8:00 PM", title: "Live jazz at Sandaga 813", note: "Thursday sessions in the arts corridor (see listing for other nights)." },
      { time: "10:00 PM", title: "Venue-hop the crawl", note: "Dozens of stages, breweries and neon rooms within a few blocks." },
    ] },
  { district: "Bishop Arts", title: "The Oak Cliff Evening",
    steps: [
      { time: "5:30 PM", title: "Bishop Arts food stroll", note: "Bakeries, taquerias and patios in North Oak Cliff." },
      { time: "7:45 PM", title: "Sunset on the Hunt Hill Bridge", note: "Skyline gold hour over the Trinity — bring a camera." },
      { time: "9:30 PM", title: "Dessert + a nightcap", note: "Back to the district for pie and a quiet patio finish." },
    ] },
  { district: "Fort Worth", title: "The Cowtown Classic",
    steps: [
      { time: "4:00 PM", title: "Stockyards cattle drive", note: "The daily longhorn drive down Exchange Avenue — free to watch." },
      { time: "6:00 PM", title: "Taproom hour at Rahr & Sons", note: "Fort Worth's original craft brewery, Near Southside." },
      { time: "8:00 PM", title: "Billy Bob's Texas", note: "Live country and bull riding at the world's largest honky-tonk." },
    ] },
];
