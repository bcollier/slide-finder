-- Image descriptions become their own searchable item kind instead of being folded into the card.
alter table sf_items drop constraint if exists sf_items_kind_check;
alter table sf_items add constraint sf_items_kind_check check (kind in ('card', 'said', 'note', 'desc'));
