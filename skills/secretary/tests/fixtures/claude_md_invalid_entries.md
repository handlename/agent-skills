# Secretary directory

## Sources

### Bad_ID
- directory: /Users/handlename/a
- query: q
- interval: 2h
- relay: yes

### supervisor
- directory: /Users/handlename/b
- query: q
- interval: 2h
- relay: yes

### unknown-key
- directory: /Users/handlename/c
- query: q
- interval: 2h
- relay: yes
- colour: blue

### missing
- directory: /Users/handlename/d
- query: q
- relay: yes

### short
- directory: /Users/handlename/e
- query: q
- interval: 10m
- relay: maybe

### nested
- directory: /Users/handlename/secretary/sub
- query: q
- interval: 2h
- relay: no

### sv
- directory: /Users/handlename/supervisor/x
- query: q
- interval: 2h
- relay: no

### short
- directory: /Users/handlename/f
- query: q
- interval: 1h
- relay: no
- query: again
