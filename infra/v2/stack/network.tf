# One VPC, two AZs. ALB and NAT in the public subnets; everything else private.
# 10.20/16 so it can peer with vivaprox-vpc (10.0/16) if v1 and v2 ever need to talk.

locals {
  azs = { a = "us-east-1a", b = "us-east-1b" }
}

resource "aws_vpc" "this" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_hostnames = true
  tags                 = { Name = local.name }
}

resource "aws_subnet" "public" {
  for_each                = local.azs
  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.value
  map_public_ip_on_launch = false
  cidr_block              = cidrsubnet(aws_vpc.this.cidr_block, 4, index(keys(local.azs), each.key))
  tags                    = { Name = "${local.name}-public-${each.key}" }
}

resource "aws_subnet" "private" {
  for_each                = local.azs
  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.value
  map_public_ip_on_launch = false
  cidr_block              = cidrsubnet(aws_vpc.this.cidr_block, 4, 8 + index(keys(local.azs), each.key))
  tags                    = { Name = "${local.name}-private-${each.key}" }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = local.name }
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${local.name}-nat" }
}

# ponytail: one NAT, so losing us-east-1a cuts private egress in both AZs. Add a
# second before this stack becomes production.
resource "aws_nat_gateway" "this" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public["a"].id
  tags          = { Name = local.name }
  depends_on    = [aws_internet_gateway.this]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.this.id
  }
  tags = { Name = "${local.name}-private" }
}

resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  for_each       = aws_subnet.private
  subnet_id      = each.value.id
  route_table_id = aws_route_table.private.id
}
