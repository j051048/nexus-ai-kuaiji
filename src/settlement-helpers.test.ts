import {describe,expect,it} from 'vitest';
import {cents,decimal,selectableItems} from './settlement-helpers';
import type {SettlementItem,SettlementBank} from './settlement-helpers';
describe('settlement deterministic inputs',()=>{
 it('keeps exact integer cents',()=>{expect(cents('0.30')).toBe(30);expect(decimal(cents('999999999999.99'))).toBe('999999999999.99');expect(()=>cents('1.001')).toThrow();expect(()=>cents('-1.00')).toThrow();});
 it('separates party direction and exhausted balances',()=>{const bank={counterparty_id:'party-a',direction:'expense'} as SettlementBank;const make=(id:string,party:string,direction:'expense'|'income',remaining:string)=>({id,counterparty_id:party,direction,remaining}) as SettlementItem;expect(selectableItems([make('ok','party-a','expense','0.10'),make('empty','party-a','expense','0.00'),make('other','party-b','expense','0.10'),make('reverse','party-a','income','0.10')],bank).map(item=>item.id)).toEqual(['ok']);expect(selectableItems([],null)).toEqual([]);});
});
