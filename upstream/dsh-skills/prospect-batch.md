# prospect-batch

Prospect multiple cities in parallel using Google Maps.

## When to use
User asks to find leads in N cities, e.g. 'Find SaaS startups in Miami, Austin, Boston'.

## Tools
- `google_maps_prospect`
- `prospex_score_batch`

## Example
```
POST /api/subagent/delegate {provider: 'prospect_city', specs: [{name:'Miami', input:{query:'SaaS startup',city:'Miami'}}, ...]}
```
