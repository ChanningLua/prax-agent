import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { formatTimestamp2 } from '../../../src/helper/format-timestamp2'

describe('formatTimestamp2', () => {
  beforeEach(() => {
    // 设置固定时间：2024-01-15 12:00:00
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2024-01-15T12:00:00.000Z'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  describe('60秒内返回"刚刚"', () => {
    it('应该在0秒时返回"刚刚"', () => {
      const now = new Date('2024-01-15T12:00:00.000Z').getTime()
      expect(formatTimestamp2(now)).toBe('刚刚')
    })

    it('应该在30秒前返回"刚刚"', () => {
      const timestamp = new Date('2024-01-15T11:59:30.000Z').getTime()
      expect(formatTimestamp2(timestamp)).toBe('刚刚')
    })

    it('应该在59秒前返回"刚刚"', () => {
      const timestamp = new Date('2024-01-15T11:59:01.000Z').getTime()
      expect(formatTimestamp2(timestamp)).toBe('刚刚')
    })
  })

  describe('1小时内返回"N分钟前"', () => {
    it('应该在1分钟前返回"1分钟前"', () => {
      const timestamp = new Date('2024-01-15T11:59:00.000Z').getTime()
      expect(formatTimestamp2(timestamp)).toBe('1分钟前')
    })

    it('应该在30分钟前返回"30分钟前"', () => {
      const timestamp = new Date('2024-01-15T11:30:00.000Z').getTime()
      expect(formatTimestamp2(timestamp)).toBe('30分钟前')
    })

    it('应该在59分钟前返回"59分钟前"', () => {
      const timestamp = new Date('2024-01-15T11:01:00.000Z').getTime()
      expect(formatTimestamp2(timestamp)).toBe('59分钟前')
    })
  })

  describe('24小时内返回"N小时前"', () => {
    it('应该在1小时前返回"1小时前"', () => {
      const timestamp = new Date('2024-01-15T11:00:00.000Z').getTime()
      expect(formatTimestamp2(timestamp)).toBe('1小时前')
    })

    it('应该在12小时前返回"12小时前"', () => {
      const timestamp = new Date('2024-01-15T00:00:00.000Z').getTime()
      expect(formatTimestamp2(timestamp)).toBe('12小时前')
    })

    it('应该在23小时前返回"23小时前"', () => {
      const timestamp = new Date('2024-01-14T13:00:00.000Z').getTime()
      expect(formatTimestamp2(timestamp)).toBe('23小时前')
    })
  })

  describe('超过24小时返回"YYYY-MM-DD"格式', () => {
    it('应该在24小时前返回"2024-01-14"', () => {
      const timestamp = new Date('2024-01-14T12:00:00.000Z').getTime()
      expect(formatTimestamp2(timestamp)).toBe('2024-01-14')
    })

    it('应该在7天前返回"2024-01-08"', () => {
      const timestamp = new Date('2024-01-08T12:00:00.000Z').getTime()
      expect(formatTimestamp2(timestamp)).toBe('2024-01-08')
    })

    it('应该在1年前返回"2023-01-15"', () => {
      const timestamp = new Date('2023-01-15T12:00:00.000Z').getTime()
      expect(formatTimestamp2(timestamp)).toBe('2023-01-15')
    })
  })

  describe('边界情况', () => {
    it('应该处理未来时间（返回刚刚）', () => {
      const futureTime = new Date('2024-01-15T12:01:00.000Z').getTime()
      expect(formatTimestamp2(futureTime)).toBe('刚刚')
    })

    it('应该处理60秒边界（60秒应返回1分钟前）', () => {
      const timestamp = new Date('2024-01-15T11:59:00.000Z').getTime()
      expect(formatTimestamp2(timestamp)).toBe('1分钟前')
    })

    it('应该处理1小时边界（3600秒应返回1小时前）', () => {
      const timestamp = new Date('2024-01-15T11:00:00.000Z').getTime()
      expect(formatTimestamp2(timestamp)).toBe('1小时前')
    })

    it('应该处理24小时边界（86400秒应返回日期格式）', () => {
      const timestamp = new Date('2024-01-14T12:00:00.000Z').getTime()
      expect(formatTimestamp2(timestamp)).toBe('2024-01-14')
    })
  })
})
